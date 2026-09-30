"""CPU contract for tokens carried through real PrefetchController methods.

No service threads, wire protocol, actual adapter I/O or death detector. The
scoped L1 facade translates legacy calls only while running a known job. It
never reconstructs tokens from a completion bitmap or a newer object.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import inspect
from pathlib import Path
import threading
import uuid

from reservation_l1_contract import ReadReservation
from lmcache.lmcache_native import Bitmap
from lmcache.v1.distributed.api import AttnWindowDesc, PrefetchMode, TrimPolicy
from lmcache.v1.distributed.error import L1Error
from lmcache.v1.distributed.storage_controllers.prefetch_controller import (
    InFlightPrefetchRequest, PrefetchController, PrefetchPhase,
)


def token_id(reservation):
    t = reservation.token
    return t.lock_id, t.epoch, t.serial


@dataclass(frozen=True)
class JobHandle:
    server: str
    sequence: int
    external_request_id: str


@dataclass(frozen=True)
class OwnedCompletion:
    handle: JobHandle
    retained_indices: tuple[int, ...]
    reservations: tuple[ReadReservation, ...]

    def popcount(self):
        return len(self.retained_indices)


@dataclass
class Job:
    handle: JobHandle
    request: InFlightPrefetchRequest
    keys: tuple
    readers: int
    mode: PrefetchMode
    tokens: dict = field(default_factory=dict)
    release_results: list = field(default_factory=list)
    abandoned: bool = False
    terminal: bool = False
    processing: bool = False
    completion: OwnedCompletion | None = None
    error: str | None = None


class _ScopedL1:
    def __init__(self, manager):
        self.manager = manager
        self.job = None

    @contextmanager
    def bind(self, job):
        if self.job is not None:
            raise RuntimeError("Nested controller scope")
        self.job = job
        try:
            yield
        finally:
            self.job = None

    def _current(self):
        if self.job is None:
            raise RuntimeError("Anonymous controller call outside job scope")
        return self.job

    def _acquire(self, method, keys, read_locks, *, require_all=False):
        job = self._current()
        if read_locks != job.readers or not set(keys) <= set(job.keys):
            raise RuntimeError("Acquisition does not match job")
        result = method(keys, readers=read_locks)
        for reservation in result.reservations:
            identity = token_id(reservation)
            if identity in job.tokens:
                raise RuntimeError("Duplicate token acquired")
            job.tokens[identity] = reservation
        # Even if a native listener raised after acquisition, captured tokens
        # remain in the failed job. No retry or publication as a good result.
        if result.error:
            raise RuntimeError(result.error)
        if require_all and any(v[0] != L1Error.SUCCESS for v in result.native_result.values()):
            raise RuntimeError("Write-to-read failed for one or more objects")
        return result.native_result

    def reserve_read(self, keys, read_locks=1):
        return self._acquire(self.manager.reserve_read_owned, keys, read_locks)

    def finish_write_and_reserve_read(self, keys, read_locks=1):
        return self._acquire(self.manager.finish_write_and_reserve_read_owned,
                             keys, read_locks, require_all=True)

    def release_tokens(self, selected):
        job = self._current()
        results = self.manager.finish_read_owned(selected)
        job.release_results.extend(results)
        # Remove only known successful references. Errors after unlock still
        # remain visible, but cannot cause those references to be retried.
        for result in results:
            if result.status == "released":
                job.tokens.pop(token_id(result.reservation))
        if any(r.status != "released" or r.error for r in results):
            raise RuntimeError("Incomplete release; job retained without automatic retry")

    def finish_read(self, keys, read_locks=1):
        job = self._current()
        keys = tuple(keys)
        selected = tuple(r for r in job.tokens.values() if r.key in keys)
        if (len(set(keys)) != len(keys) or read_locks != job.readers
                or Counter(r.key for r in selected) != Counter({k: read_locks for k in keys})):
            raise RuntimeError("Release does not match captured reader slots")
        self.release_tokens(selected)
        return {key: L1Error.SUCCESS for key in keys}

    def finish_write(self, keys):
        self._current()
        result = self.manager.finish_write(keys)
        if any(value != L1Error.SUCCESS for value in result.values()):
            raise RuntimeError("Writer finalization failed")
        return result

    def finish_write_and_delete(self, keys):
        self._current()
        result = self.manager.finish_write_and_delete(keys)
        if any(value != L1Error.SUCCESS for value in result.values()):
            raise RuntimeError("Failed load buffer cleanup failed")
        return result

    def touch_keys(self, keys):
        self._current()
        return self.manager.touch_keys(keys)


class OwnedPrefetchHarness(PrefetchController):
    """Run pinned native control flow with an explicit token owner per job.

    Tests supply L2 adapters and terminal results; no background thread runs.
    QUERY transfers an immutable completion once. Abandon before completion
    blocks QUERY but cannot release while an adapter task is pending.
    """
    def __init__(self, l1, event_bus, *, adapters=None, descriptors=None):
        digest = hashlib.sha256(Path(inspect.getsourcefile(PrefetchController)).read_bytes()).hexdigest()
        if digest != "484609bd8146bcfdfacb35c41af9604bc0173d8434494cc9d016b15267e8b20e":
            raise RuntimeError("Unsupported controller source: " + digest)
        # Only fields used by the actual _lock_l1_keys/_poll_load_results/
        # _finish_request/_complete_request/query_prefetch_result paths.
        self._l1_manager = _ScopedL1(l1)
        self._event_bus = event_bus
        self._l2_adapters = adapters or {}
        self._adapter_descriptors = descriptors or {}
        self._in_flight_requests = {}
        self._completed_results = {}
        self._completed_lookups = {}
        self._prefetch_results_lock = threading.Lock()
        self._prefetch_results_cv = threading.Condition(self._prefetch_results_lock)
        self._lookup_results_lock = threading.Lock()
        self._status_in_flight_count = 0
        self._status_lookup_phase_count = 0
        self._status_load_phase_count = 0
        self.gate = threading.RLock()
        self.jobs = {}
        self._next_sequence = 0
        self._server = uuid.uuid4().hex
        self.reclaimed = 0

    def _job(self, handle):
        job = self.jobs.get(handle.sequence)
        return job if job is not None and job.handle == handle else None

    def begin(self, external_request_id, keys, *, readers=1,
              policy=TrimPolicy.PREFIX, mode=PrefetchMode.LOOKUP, attn_desc=None):
        keys = tuple(keys)
        if len(set(keys)) != len(keys) or type(readers) is not int or not 1 <= readers <= 128:
            raise ValueError("Unique keys and reader count in [1,128] required")
        with self.gate:
            handle = JobHandle(self._server, self._next_sequence, external_request_id)
            self._next_sequence += 1
            request = InFlightPrefetchRequest(handle.sequence, list(keys), PrefetchPhase.LOOKUP,
                num_kv_readers=readers, policy=policy, mode=mode,
                attn_desc=attn_desc or AttnWindowDesc([-1]))
            job = Job(handle, request, keys, readers, mode)
            self.jobs[handle.sequence] = job
            self._in_flight_requests[handle.sequence] = request
            self._status_in_flight_count += 1
            self._status_lookup_phase_count += 1
            job.processing = True
            try:
                with self._l1_manager.bind(job):
                    request.l1_readlocks = super()._lock_l1_keys(
                        list(keys), readers, policy, request.attn_desc)
            except Exception as exc:
                job.error = repr(exc)
            finally:
                job.processing = False
            return handle

    def poll_load(self, handle, signaled_adapters):
        with self.gate:
            job = self._job(handle)
            if job is None or job.error or job.terminal or job.processing:
                return False
            job.processing = True
            try:
                super()._poll_load_results(job.request, signaled_adapters)
                return job.request.all_loads_done()
            except Exception as exc:
                job.error = repr(exc)
                return False
            finally:
                job.processing = False

    def finish(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None or job.error or job.terminal or job.processing:
                return False
            request = job.request
            if not request.all_lookups_done() or not request.all_loads_done():
                return False  # Cancellation intent is not an I/O terminal result.
            job.processing = True
            try:
                if (tuple(request.keys) != job.keys or request.num_kv_readers != job.readers
                        or request.mode != job.mode):
                    raise RuntimeError("Job identity/layout changed")
                with self._l1_manager.bind(job):
                    super()._finish_request(request)
            except Exception as exc:
                job.error = repr(exc)
                return False
            finally:
                job.processing = False
            if job.abandoned:
                self._reap(job)
            return job.error is None

    def _complete_request(self, request_id, bitmap):
        job = self.jobs[request_id]
        indices = tuple(bitmap.get_indices_list())
        expected = (Counter() if job.mode == PrefetchMode.WARM else
                    Counter({job.keys[i]: job.readers for i in indices}))
        if Counter(r.key for r in job.tokens.values()) != expected:
            raise RuntimeError("Retained bitmap and owned reader slots disagree")
        result = OwnedCompletion(job.handle, indices, tuple(job.tokens.values()))
        job.completion = result  # Retain the terminal evidence if later release fails.
        super()._complete_request(request_id, result)
        job.terminal = True

    def query_owned(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None or job.error or job.abandoned or job.processing or not job.terminal:
                return None
            result = super().query_prefetch_result(handle.sequence)
            if result is None:
                raise RuntimeError("Terminal job lost its completion")
            del self.jobs[handle.sequence]  # Consumer now owns the returned tokens.
            return result

    def abandon(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None:
                return False  # Already transferred, or another incarnation.
            job.abandoned = True
            if job.terminal and not job.error:
                self._reap(job)
            return True

    def _reap(self, job):
        if job.processing or job.error:
            return
        job.processing = True
        try:
            result = super().query_prefetch_result(job.handle.sequence)
            if result is None:
                raise RuntimeError("Abandoned terminal job lost its completion")
            with self._l1_manager.bind(job):
                self._l1_manager.release_tokens(result.reservations)
            del self.jobs[job.handle.sequence]
            self.reclaimed += 1
        except Exception as exc:
            job.error = repr(exc)  # Keep tokens, known releases and uncertainty.
        finally:
            job.processing = False

    def query_prefetch_result(self, *args, **kwargs):
        raise RuntimeError("Use query_owned with a job handle")

    def start(self):
        raise RuntimeError("CPU contract only; no service installation")

    def stop(self):
        raise RuntimeError("Owned writer/shutdown protocol is not implemented")
