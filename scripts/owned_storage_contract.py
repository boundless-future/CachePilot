"""CPU ownership contract around pinned StorageManager submit/fold/query.

Initial L1 tokens and a destructively consumed controller completion have one
outer owner. No service threads, RPC, writer identities or actual L2 I/O.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import inspect
from pathlib import Path
import threading
import uuid

from lmcache.lmcache_native import Bitmap
from lmcache.v1.distributed.api import ObjectKey, PrefetchMode, TrimPolicy
from lmcache.v1.distributed.storage_manager import StorageManager
from owned_prefetch_contract import JobHandle, OwnedCompletion, _ScopedL1, token_id


@dataclass
class StorageJob:
    handle: JobHandle
    keys: tuple
    readers: int
    mode: PrefetchMode
    tokens: dict = field(default_factory=dict)
    release_results: list = field(default_factory=list)
    native_handle: object | None = None
    initial_indices: tuple = ()
    downstream: JobHandle | None = None
    downstream_keys: tuple = ()
    downstream_completion: OwnedCompletion | None = None
    abandoned: bool = False
    terminal: bool = False
    processing: bool = False
    completion: OwnedCompletion | None = None
    error: str | None = None


class _ControllerBridge:
    def __init__(self, storage, controller):
        self.storage = storage
        self.controller = controller

    def submit_prefetch_request(self, spec):
        job = self.storage._l1_manager._current()
        if job.downstream is not None:
            raise RuntimeError("Only one downstream submission per storage job")
        job.downstream_keys = tuple(spec.keys)
        job.downstream = self.controller.begin(job.handle.external_request_id,
            spec.keys, readers=spec.num_kv_readers, policy=spec.policy,
            mode=spec.mode, attn_desc=spec.attn_desc)
        return job.downstream.sequence

    def query_prefetch_result(self, request_id):
        job = self.storage._l1_manager._current()
        if job.downstream is None or request_id != job.downstream.sequence:
            raise RuntimeError("Controller query does not match storage owner")
        child = self.controller._job(job.downstream)
        if child is not None and child.error:
            raise RuntimeError("Downstream unresolved: " + child.error)
        result = self.controller.query_owned(job.downstream)
        if result is None:
            if child is None:
                raise RuntimeError("Downstream result disappeared before transfer")
            return None
        # Record the transfer before validating/merging; failures must retain
        # both the original completion and every newly transferred token.
        job.downstream_completion = result
        collisions = False
        for reservation in result.reservations:
            identity = token_id(reservation)
            collisions |= identity in job.tokens
            job.tokens.setdefault(identity, reservation)
        if collisions or result.handle != job.downstream:
            raise RuntimeError("Invalid downstream ownership transfer")
        indices = result.retained_indices
        self.storage._indices(indices, len(job.downstream_keys))
        expected = (Counter() if job.mode == PrefetchMode.WARM else
                    Counter({job.downstream_keys[i]: job.readers for i in indices}))
        if Counter(r.key for r in result.reservations) != expected:
            raise RuntimeError("Downstream tokens do not match local indices")
        bitmap = Bitmap(len(job.downstream_keys))
        bitmap.batched_set(indices)
        return bitmap


class OwnedStorageHarness(StorageManager):
    """Use real storage control flow, with explicit one-time ownership.

    Caller supplies a CPU L1/controller fixture and drives controller terminal
    progress. advance() collects a ready child even after abandon; it never
    interprets cancellation intent as I/O completion.
    """
    def __init__(self, l1, controller, event_bus):
        digest = hashlib.sha256(Path(inspect.getsourcefile(StorageManager)).read_bytes()).hexdigest()
        if digest != "628136dc664be9f32efc6cc4bb29e62c5e7a7702b8c1a708a1ac14b1fde8a430":
            raise RuntimeError("Unsupported StorageManager source: " + digest)
        self._l1_manager = _ScopedL1(l1)
        self._prefetch_controller = _ControllerBridge(self, controller)
        self._l2_adapters = controller._l2_adapters
        self._adapters_lock = threading.Lock()
        self._event_bus = event_bus
        self.gate = threading.RLock()
        self.jobs = {}
        self._server = uuid.uuid4().hex
        self._next_sequence = 0
        self.reclaimed = 0

    def _job(self, handle):
        job = self.jobs.get(handle.sequence)
        return job if job is not None and job.handle == handle else None

    @staticmethod
    def _indices(indices, size):
        if (len(set(indices)) != len(indices)
                or any(type(i) is not int or not 0 <= i < size for i in indices)):
            raise RuntimeError("Duplicate or out-of-range indices")

    def _validate_handle(self, job):
        handle = job.native_handle
        size = len(job.keys)
        if (handle is None or handle.total_requested_keys != size
                or handle.external_request_id != job.handle.external_request_id):
            raise RuntimeError("Storage handle identity/layout changed")
        self._indices(handle.l1_found_indices, size)
        self._indices(handle.l2_orig_indices, size)
        if handle.l1_found_indices != job.initial_indices:
            raise RuntimeError("Initial retained set changed")
        if job.downstream is None:
            if handle.prefetch_request_id != -1 or handle.l2_orig_indices:
                raise RuntimeError("Pure L1 handle claims a controller result")
        elif (handle.prefetch_request_id != job.downstream.sequence
                or tuple(job.keys[i] for i in handle.l2_orig_indices) != job.downstream_keys
                or set(handle.l1_found_indices) & set(handle.l2_orig_indices)):
            raise RuntimeError("L2 local-to-original mapping changed")

    def submit_owned(self, spec, external_request_id="", *, skip_l2=False):
        # Native dispatch rejects some policies only AFTER reserving L1.
        # This contract supports the paths exercised here; reject early.
        keys = tuple(spec.keys)
        readers = spec.num_kv_readers
        if (len(set(keys)) != len(keys) or type(readers) is not int or not 1 <= readers <= 128
                or spec.policy not in (TrimPolicy.PREFIX, TrimPolicy.SPARSE)
                or spec.mode not in (PrefetchMode.LOOKUP, PrefetchMode.WARM)):
            raise ValueError("Unsupported policy/mode, duplicate keys or reader count")
        desc = spec.attn_desc
        stride = desc.world_size * desc.num_object_groups
        if spec.policy == TrimPolicy.PREFIX:
            if stride < 1 or len(keys) % stride:
                raise ValueError("PREFIX needs complete chunk/group/rank rows")
            # Real IPC keys encode world/local topology into kv_rank; earlier
            # CPU fixtures use plain ranks. Require one complete convention.
            ranks = (list(range(desc.world_size)),
                     [ObjectKey.ComputeKVRank(desc.world_size, r, desc.world_size, r)
                      for r in range(desc.world_size)])
            layouts = [[(group, rank) for group in range(desc.num_object_groups)
                        for rank in convention] for convention in ranks]
            if not any(all((key.object_group_id, key.kv_rank) == layout[i % stride]
                           for i, key in enumerate(keys)) for layout in layouts):
                raise ValueError("PREFIX group/rank ordering does not match descriptor")
        snapshot = replace(spec, keys=list(keys), attn_desc=deepcopy(desc),
                           group_layout_descs=dict(spec.group_layout_descs))
        with self.gate:
            if self._l1_manager.job is not None:
                raise RuntimeError("Reentrant storage submission")
            handle = JobHandle(self._server, self._next_sequence, external_request_id)
            self._next_sequence += 1
            job = StorageJob(handle, keys, readers, spec.mode)
            self.jobs[handle.sequence] = job
            job.processing = True
            try:
                with self._l1_manager.bind(job):
                    job.native_handle = super().submit_prefetch_task(
                        snapshot, external_request_id, skip_l2)
                job.initial_indices = job.native_handle.l1_found_indices
                self._validate_handle(job)
                expected = (Counter() if job.mode == PrefetchMode.WARM else
                            Counter({keys[i]: readers for i in job.initial_indices}))
                if Counter(r.key for r in job.tokens.values()) != expected:
                    raise RuntimeError("Initial L1 tokens disagree with retained set")
            except Exception as exc:
                job.error = repr(exc)
            finally:
                job.processing = False
            if job.downstream is None and not job.error:
                self.advance(handle)
            return handle

    def _combine_found(self, handle, l2_local):
        job = self._l1_manager._current()
        self._validate_handle(job)
        if handle != job.native_handle:
            raise RuntimeError("Wrong storage handle passed to merge")
        if l2_local is not None:
            self._indices(tuple(l2_local.get_indices_list()), len(handle.l2_orig_indices))
        return super()._combine_found(handle, l2_local)

    def advance(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None or job.error or job.processing:
                return False
            if not job.terminal:
                job.processing = True
                try:
                    self._validate_handle(job)
                    with self._l1_manager.bind(job):
                        bitmap = super().query_prefetch_status(job.native_handle)
                    if bitmap is None:
                        return False
                    indices = tuple(bitmap.get_indices_list())
                    expected = (Counter() if job.mode == PrefetchMode.WARM else
                                Counter({job.keys[i]: job.readers for i in indices}))
                    if Counter(r.key for r in job.tokens.values()) != expected:
                        raise RuntimeError("Merged bitmap and owned reader slots disagree")
                    job.completion = OwnedCompletion(job.handle, indices, tuple(job.tokens.values()))
                    job.terminal = True
                except Exception as exc:
                    job.error = repr(exc)
                    return False
                finally:
                    job.processing = False
            if job.abandoned:
                self._reap(job)
            return job.error is None

    def query_owned(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None or job.error or job.abandoned or job.processing:
                return None
            if not self.advance(handle) or job.abandoned:
                return None
            del self.jobs[handle.sequence]
            return job.completion

    def abandon(self, handle):
        with self.gate:
            job = self._job(handle)
            if job is None:
                return False
            job.abandoned = True
            self.advance(handle)
            return True

    def _reap(self, job):
        if job.processing or job.error:
            return
        job.processing = True
        try:
            with self._l1_manager.bind(job):
                self._l1_manager.release_tokens(job.completion.reservations)
            del self.jobs[job.handle.sequence]
            self.reclaimed += 1
        except Exception as exc:
            job.error = repr(exc)
        finally:
            job.processing = False

    def submit_prefetch_task(self, *args, **kwargs):
        raise RuntimeError("Use submit_owned")

    def query_prefetch_status(self, *args, **kwargs):
        raise RuntimeError("Use query_owned")

    def start(self):
        raise RuntimeError("CPU contract only; no service installation")

    def close(self):
        raise RuntimeError("Owned writer/shutdown protocol is not implemented")
