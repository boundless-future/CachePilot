"""CPU LookupModule/worker-slot contract; no wire or data/DMA access.

QUERY exposes tickets, while this registry owns every token until a slot's
explicit terminal acknowledgement. Cancel never implies transfer completion.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import inspect
from pathlib import Path
import threading
import uuid

from lmcache.lmcache_native import Bitmap
from lmcache.v1.distributed.api import ObjectKey
from lmcache.v1.distributed.bitmap_ops.fold import fold_unfold_ranked
from lmcache.v1.multiprocess.modules.lookup import LookupModule
from owned_prefetch_contract import JobHandle, token_id


@dataclass(frozen=True)
class Worker:
    incarnation: str
    rank: int


@dataclass(frozen=True)
class ReaderTicket:
    lookup: JobHandle
    worker: Worker


@dataclass(frozen=True)
class LookupCompletion:
    handle: JobHandle
    hit_chunks: int
    tickets: tuple[ReaderTicket, ...]


@dataclass(frozen=True)
class RetrievalClaim:
    ticket: ReaderTicket
    reservations: tuple


@dataclass
class ReaderSlot:
    ticket: ReaderTicket
    reservations: tuple
    state: str = "offered"
    cancelled: bool = False
    outcome: bool | None = None


@dataclass
class LookupJob:
    handle: JobHandle
    workers: tuple
    lookup_key: object | None = None
    chunk_size: int = 0
    ranks: dict = field(default_factory=dict)
    keys: tuple = ()
    readers: int = 1
    native_job: object | None = None
    storage_handle: JobHandle | None = None
    storage_completion: object | None = None
    tokens: dict = field(default_factory=dict)
    release_results: list = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    completion: LookupCompletion | None = None
    abandoned: bool = False
    processing: bool = False
    end_requested: bool = False
    end_processed: bool = False
    error: str | None = None


class _StorageBridge:
    def __init__(self, lookup, storage):
        self.lookup = lookup
        self.storage = storage

    def submit_prefetch_task(self, spec, external_request_id):
        job = self.lookup._current()
        if job.storage_handle is not None:
            raise RuntimeError("Duplicate storage submission")
        if (spec.attn_desc.world_size != len(set(w.rank for w in job.workers))
                or "aux" in spec.attn_desc.group_kinds):
            raise RuntimeError("Unsupported descriptor topology or aux group")
        job.keys = tuple(spec.keys)
        job.readers = spec.num_kv_readers
        expected = Counter({key.kv_rank: job.readers for key in job.keys})
        actual = Counter(job.ranks[w.rank] for w in job.workers)
        if any(actual[rank] != count for rank, count in expected.items()):
            raise RuntimeError("Worker topology does not cover reserved reader slots")
        job.storage_handle = self.storage.submit_owned(spec, external_request_id)
        child = self.storage._job(job.storage_handle)
        if child.error:
            raise RuntimeError("Storage submission unresolved: " + child.error)
        return child.native_handle

    def query_prefetch_status(self, handle):
        job = self.lookup._current()
        if job.native_job is None or handle != job.native_job.handle:
            raise RuntimeError("Wrong native lookup handle")
        if job.storage_handle is None:
            if handle.total_requested_keys:
                raise RuntimeError("Nonempty lookup has no storage owner")
            return Bitmap(0)
        child = self.storage._job(job.storage_handle)
        if child is not None and child.error:
            raise RuntimeError("Storage result unresolved: " + child.error)
        completion = self.storage.query_owned(job.storage_handle)
        if completion is None:
            if child is None:
                raise RuntimeError("Storage completion disappeared")
            if child.error:
                raise RuntimeError("Storage result unresolved: " + child.error)
            return None
        job.storage_completion = completion
        collisions = False
        for reservation in completion.reservations:
            identity = token_id(reservation)
            collisions |= identity in job.tokens
            job.tokens.setdefault(identity, reservation)
        if collisions or completion.handle != job.storage_handle:
            raise RuntimeError("Invalid storage ownership transfer")
        self.storage._indices(completion.retained_indices, len(job.keys))
        expected = Counter({job.keys[i]: job.readers for i in completion.retained_indices})
        if Counter(r.key for r in completion.reservations) != expected:
            raise RuntimeError("Storage tokens disagree with retained indices")
        bitmap = Bitmap(len(job.keys))
        bitmap.batched_set(completion.retained_indices)
        return bitmap

    def touch_l1_keys(self, keys):
        self.lookup._current()
        return self.storage._l1_manager.manager.touch_keys(keys)


class OwnedLookupHarness(LookupModule):
    """Run real lookup, query folding/session recording and END methods.

    Explicit Worker incarnations are supplied by the CPU fixture. A ticket
    claims the whole retained shard once; ranged/split retrieves need a later
    protocol. finish_retrieve is a simulated terminal ack, never actual DMA.
    """
    def __init__(self, ctx, storage):
        digest = hashlib.sha256(Path(inspect.getsourcefile(LookupModule)).read_bytes()).hexdigest()
        if digest != "cdd6917c6b3e1aed283313ca7a696ded9e8ad0e7309df7ce4afb0285103b7866":
            raise RuntimeError("Unsupported LookupModule source: " + digest)
        self.storage = storage
        ctx.storage_manager = _StorageBridge(self, storage)
        self._ctx = ctx
        self._prefetch_jobs = {}
        self._prefetch_job_lock = threading.Lock()
        self.gate = threading.RLock()
        self.jobs = {}
        self._scope = None
        self._server = uuid.uuid4().hex
        self._sequence = 0
        self._latest = {}
        self.closed = 0

    def _current(self):
        if self._scope is None:
            raise RuntimeError("Anonymous lookup storage operation")
        return self._scope

    def _job(self, handle):
        job = self.jobs.get(handle.sequence)
        return job if job is not None and job.handle == handle else None

    def _register_prefetch_job(self, native_job):
        job = self._current()
        if job.native_job is not None or native_job.request_id != job.handle.external_request_id:
            raise RuntimeError("Duplicate or wrong native job registration")
        native_job.attn_desc = deepcopy(native_job.attn_desc)
        job.native_job = native_job
        super()._register_prefetch_job(native_job)

    def begin(self, key, workers):
        workers = tuple(workers)
        readers = key.require_num_kv_readers()
        if (key.worker_id is not None or type(readers) is not int or not 1 <= readers <= 128
                or not workers or len(set(workers)) != len(workers)
                or any(not isinstance(w, Worker) or not w.incarnation or type(w.rank) is not int
                       or not 0 <= w.rank < key.world_size for w in workers)
                or Counter(w.rank for w in workers) != Counter({r: readers for r in range(key.world_size)})):
            raise ValueError("Explicit, unique worker incarnations covering every rank/reader required")
        with self.gate:
            if self._scope is not None:
                raise RuntimeError("Reentrant lookup submission")
            if any(j.handle.external_request_id == key.request_id and not j.abandoned
                   for j in self.jobs.values()):
                raise RuntimeError("Previous generation still owns reader slots")
            handle = JobHandle(self._server, self._sequence, key.request_id)
            self._sequence += 1
            job = LookupJob(handle, workers, lookup_key=deepcopy(key),
                            chunk_size=self._ctx.chunk_size, readers=readers)
            job.ranks = {r: ObjectKey.ComputeKVRank(key.world_size, r, key.world_size, r)
                         for r in range(key.world_size)}
            self.jobs[handle.sequence] = job
            self._latest[key.request_id] = handle
            job.processing = True
            self._scope = job
            try:
                super().lookup(key, key.world_size)
                if job.native_job is None:
                    raise RuntimeError("LOOKUP did not register a job")
            except Exception as exc:
                job.error = repr(exc)
            finally:
                self._scope = None
                job.processing = False
            if job.abandoned:
                self.advance(handle)
            return handle

    def _release(self, job, reservations):
        results = self.storage._l1_manager.manager.finish_read_owned(reservations)
        job.release_results.extend(results)
        for result in results:
            if result.status == "released":
                job.tokens.pop(token_id(result.reservation))
        if any(r.status != "released" or r.error for r in results):
            raise RuntimeError("Incomplete token release; retained without automatic retry")

    def query_owned(self, handle):
        with self.gate:
            job = self._job(handle)
            if (job is None or job.error or job.abandoned or job.processing or job.completion
                    or self._scope is not None):
                return None
            job.processing = True
            self._scope = job
            try:
                if self._prefetch_jobs.get(handle.external_request_id) is not job.native_job:
                    raise RuntimeError("Native lookup generation changed")
                hits = super().query_prefetch_status(handle.external_request_id)
                if hits is None:
                    return None
                # The native query folds the combined bitmap but ignores its
                # retain set. Release old sliding-window hits no reader uses.
                indices = ()
                if job.storage_completion is not None:
                    found = Bitmap(len(job.keys))
                    found.batched_set(job.storage_completion.retained_indices)
                    desc = job.native_job.attn_desc
                    folded, retain = fold_unfold_ranked(found,
                        len(job.keys) // (desc.world_size * desc.num_object_groups),
                        desc.world_size, desc.num_chunks_in_sw)
                    if hits != folded:
                        raise RuntimeError("Native hit count changed")
                    indices = tuple(retain.get_indices_list())
                retained_keys = {job.keys[i] for i in indices}
                self._release(job, tuple(r for r in job.tokens.values() if r.key not in retained_keys))
                expected = Counter({key: job.readers for key in retained_keys})
                if Counter(r.key for r in job.tokens.values()) != expected:
                    raise RuntimeError("Retained lookup reader slots disagree")
                # Assign the acquired tokens, one per matching key per worker.
                available = {key: iter(tuple(r for r in job.tokens.values() if r.key == key))
                             for key in retained_keys}
                for worker in job.workers:
                    reservations = tuple(next(available[key]) for key in job.keys
                                         if key in retained_keys and key.kv_rank == job.ranks[worker.rank])
                    if reservations:
                        ticket = ReaderTicket(handle, worker)
                        job.slots[ticket] = ReaderSlot(ticket, reservations)
                assigned = [token_id(r) for slot in job.slots.values() for r in slot.reservations]
                if len(set(assigned)) != len(assigned) or set(assigned) != set(job.tokens):
                    raise RuntimeError("Worker slot assignment is not an exact partition")
                job.completion = LookupCompletion(handle, hits, tuple(job.slots))
            except Exception as exc:
                job.error = repr(exc)
                return None
            finally:
                self._scope = None
                job.processing = False
                if job.abandoned and not job.error:
                    self.advance(handle)
            if job.abandoned:
                return None
            result = job.completion
            self._close_if_drained(job)
            return result

    def claim_retrieve(self, ticket):
        with self.gate:
            job = self._job(ticket.lookup)
            if (job is None or job.error or job.abandoned or job.processing
                    or self._scope is not None):
                return None
            slot = job.slots.get(ticket)
            if slot is None or slot.state != "offered":
                return None
            slot.state = "running"
            return RetrievalClaim(ticket, slot.reservations)

    def finish_retrieve(self, ticket, *, succeeded):
        if type(succeeded) is not bool:
            raise ValueError("An explicit terminal outcome is required")
        with self.gate:
            job = self._job(ticket.lookup)
            if job is None or job.error or job.processing or self._scope is not None:
                return False
            slot = job.slots.get(ticket)
            if slot is None or slot.state != "running":
                return False
            job.processing = True
            self._scope = job
            try:
                slot.outcome = succeeded
                slot.state = "terminal"  # Record terminal evidence before release.
                self._release(job, slot.reservations)
                slot.state = "closed"
            except Exception as exc:
                job.error = repr(exc)
                return False
            finally:
                self._scope = None
                job.processing = False
            if job.abandoned:
                self.advance(job.handle)
            self._close_if_drained(job)
            return True

    def abandon(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None:
                return False
            job.abandoned = True
            if self._prefetch_jobs.get(handle.external_request_id) is job.native_job:
                self._prefetch_jobs.pop(handle.external_request_id, None)
            for slot in job.slots.values():
                slot.cancelled = True
            self.advance(handle)
            return True

    def advance(self, handle):
        with self.gate:
            job = self._job(handle)
            if (job is None or job.error or job.processing or not job.abandoned
                    or self._scope is not None):
                return False
            job.processing = True
            self._scope = job
            try:
                if job.end_requested and not job.end_processed:
                    job.end_processed = True
                    if self._latest.get(handle.external_request_id) == handle:
                        super().end_session(handle.external_request_id)
                if self._prefetch_jobs.get(handle.external_request_id) is job.native_job:
                    self._prefetch_jobs.pop(handle.external_request_id, None)
                if job.storage_completion is None and job.storage_handle is not None:
                    if self.storage._job(job.storage_handle) is not None:
                        self.storage.abandon(job.storage_handle)
                        self.storage.advance(job.storage_handle)
                    child = self.storage._job(job.storage_handle)
                    if child is not None:
                        if child.error:
                            raise RuntimeError("Abandoned storage unresolved: " + child.error)
                        return False
                for slot in job.slots.values():
                    if slot.state == "offered":
                        self._release(job, slot.reservations)
                        slot.state = "closed"
                self._close_if_drained(job)
                return not job.tokens
            except Exception as exc:
                job.error = repr(exc)
                return False
            finally:
                self._scope = None
                job.processing = False

    def end_owned(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None:
                return False
            job.end_requested = True
            return self.abandon(handle)

    def _close_if_drained(self, job):
        if (self._job(job.handle) is job and not job.tokens and not job.error
                and (job.completion is not None or job.abandoned)
                and all(s.state == "closed" for s in job.slots.values())):
            self.jobs.pop(job.handle.sequence, None)
            request_id = job.handle.external_request_id
            if not any(j.handle.external_request_id == request_id for j in self.jobs.values()):
                self._latest.pop(request_id, None)
            self.closed += 1

    def lookup(self, *args, **kwargs):
        raise RuntimeError("Use begin with explicit worker incarnations")

    def query_prefetch_status(self, *args, **kwargs):
        raise RuntimeError("Use query_owned with an incarnation handle")

    def query_prefetch_lookup_hits(self, *args, **kwargs):
        raise RuntimeError("Anonymous hit query forbidden")

    def wait_prefetch_status(self, *args, **kwargs):
        raise RuntimeError("Anonymous wait forbidden")

    def free_lookup_locks(self, *args, **kwargs):
        raise RuntimeError("Anonymous key/count release forbidden")

    def end_session(self, *args, **kwargs):
        raise RuntimeError("Use end_owned")

    def read_retrieve(self, *args, **kwargs):
        raise RuntimeError("Token validation + memory lease/DMA lifetime not implemented")

    def close(self):
        raise RuntimeError("CPU contract only; shutdown ownership not implemented")
