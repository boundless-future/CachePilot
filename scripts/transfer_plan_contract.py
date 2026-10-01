"""Owned server transfer plans. CPU contract, no GPU block ownership or wire.

Supported: full-attention object groups, arbitrary object/kernel grouping,
chunk-aligned suffix ending at Lookup hits, block-aligned APC skip.
All original shard pins remain live through one whole-plan completion.
"""
from dataclasses import dataclass

from lmcache.v1.multiprocess.custom_types import IPCCacheServerKey
from owned_lookup_contract import ReaderTicket, Worker
from transfer_completion_contract import TransferCompletionHarness


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


@dataclass(frozen=True)
class KernelLayout:
    object_group: int
    block_size: int
    capacity: int


@dataclass(frozen=True)
class RegisteredLayout:
    """Trusted server snapshot, not client-supplied layout authorization."""
    incarnation: str
    worker: Worker
    model_name: str
    world_size: int
    chunk_size: int
    object_bytes: tuple[int, ...]
    kernels: tuple[KernelLayout, ...]


@dataclass(frozen=True)
class GroupTransfer:
    object_group: int
    keys: tuple
    buffers: tuple
    kernel_ids: tuple[int, ...]


@dataclass(frozen=True)
class TransferPlan:
    handle: object
    layout: RegisteredLayout
    start: int
    end: int
    skip_first_n_tokens: int
    block_ids: tuple[tuple[int, ...], ...]
    groups: tuple[GroupTransfer, ...]


class PlannedTransferHarness(TransferCompletionHarness):
    def __init__(self, lookup):
        super().__init__(lookup)
        self.layouts = {}
        self.registrations = set()
        self.plans = {}

    def register_layout(self, layout):
        if (not isinstance(layout, RegisteredLayout)
                or not isinstance(layout.worker, Worker)
                or not isinstance(layout.worker.incarnation, str) or not layout.worker.incarnation
                or not isinstance(layout.incarnation, str) or not layout.incarnation
                or not isinstance(layout.model_name, str) or not layout.model_name
                or not integer(layout.world_size, 1)
                or not integer(layout.worker.rank) or layout.worker.rank >= layout.world_size
                or not integer(layout.chunk_size, 1)
                or type(layout.object_bytes) is not tuple or not layout.object_bytes
                or any(not integer(n, 1) for n in layout.object_bytes)
                or type(layout.kernels) is not tuple or not layout.kernels):
            raise ValueError("Invalid trusted layout")
        for kernel in layout.kernels:
            if (not isinstance(kernel, KernelLayout) or not integer(kernel.object_group)
                    or kernel.object_group >= len(layout.object_bytes)
                    or not integer(kernel.block_size, 1)
                    or layout.chunk_size % kernel.block_size
                    or not integer(kernel.capacity, 1)):
                raise ValueError("Invalid kernel layout")
        if {k.object_group for k in layout.kernels} != set(range(len(layout.object_bytes))):
            raise ValueError("Every object group must have a kernel group")
        with self.gate:
            identity = (layout.worker, layout.incarnation)
            if identity in self.registrations:
                raise ValueError("Registration incarnation cannot be reused")
            self.registrations.add(identity)
            self.layouts[layout.worker] = layout

    def prepare_plan(self, ticket, key, block_ids, *, registration, skip_first_n_tokens=0):
        """Invalid client metadata is rejected before claiming a reader slot.

        The client selects blocks, never source buffers/reservation tokens.
        Range and keys come from the captured Lookup snapshot, not session state.
        """
        if not isinstance(ticket, ReaderTicket):
            raise ValueError("Explicit reader ticket required")
        with self.gate, self.lookup.gate:
            job = self.lookup._job(ticket.lookup)
            if job is None or job.completion is None or ticket not in job.slots:
                raise ValueError("Unknown original reader slot")
            layout = self.layouts.get(ticket.worker)
            if layout is None or registration != layout.incarnation:
                raise ValueError("Worker registration changed or missing")
            original = job.lookup_key
            if not isinstance(key, IPCCacheServerKey) or original is None:
                raise ValueError("Captured IPC key required")
            # IPC equality ignores request_id; compare identity fields explicitly.
            if (key.request_id != ticket.lookup.external_request_id
                    or key.worker_id != ticket.worker.rank or type(key.worker_id) is not int
                    or key.model_name != original.model_name
                    or key.model_name != layout.model_name
                    or key.cache_salt != original.cache_salt
                    or key.world_size != original.world_size or key.world_size != layout.world_size
                    or type(key.world_size) is not int
                    or key.num_kv_readers != job.readers or type(key.num_kv_readers) is not int
                    or key.token_ids != original.token_ids
                    or job.chunk_size != layout.chunk_size):
                raise ValueError("Request identity or tokens disagree with Lookup")
            desc = job.native_job.attn_desc
            if (original.start != 0 or desc.num_object_groups != len(layout.object_bytes)
                    or any(w != -1 for w in desc.num_chunks_in_sw)
                    or any(k != "attention" for k in desc.group_kinds)):
                raise ValueError("Only zero-origin full-attention layouts are supported")
            chunk = job.chunk_size
            if (not integer(key.start) or not integer(key.end, 1)
                    or key.start % chunk or key.end % chunk
                    or key.end != job.completion.hit_chunks * chunk
                    or key.end > original.end or key.start >= key.end
                    or not integer(skip_first_n_tokens)
                    or skip_first_n_tokens >= key.end - key.start):
                raise ValueError("Expected nonempty aligned suffix ending at Lookup hits")
            if not isinstance(block_ids, (tuple, list)) or len(block_ids) != len(layout.kernels):
                raise ValueError("Block lists must match kernel groups")
            blocks = []
            for kernel, ids in zip(layout.kernels, block_ids, strict=True):
                if (not isinstance(ids, (tuple, list))
                        or len(ids) != (key.end - key.start) // kernel.block_size
                        or any(not integer(i) or i >= kernel.capacity for i in ids)
                        or len(set(ids)) != len(ids)
                        or skip_first_n_tokens % kernel.block_size):
                    raise ValueError("Invalid block count, bounds, aliases or APC skip")
                blocks.append(tuple(ids))
            # job.keys was captured chunk-major at original Lookup submission.
            by_group = []
            selected = []
            shard = job.slots[ticket].reservations
            shard_keys = {r.key for r in shard}
            for group in range(len(layout.object_bytes)):
                all_keys = tuple(k for k in job.keys
                                 if k.kv_rank == job.ranks[ticket.worker.rank]
                                 and k.object_group_id == group)
                group_keys = all_keys[key.start // chunk:key.end // chunk]
                if (len(group_keys) != (key.end - key.start) // chunk
                        or any(k not in shard_keys for k in group_keys)):
                    raise ValueError("Requested range is not owned by original shard")
                by_group.append(group_keys)
                selected.extend(group_keys)
            if len(set(selected)) != len(selected):
                raise ValueError("Ambiguous duplicate source keys")
            handle = super().prepare(ticket)
            if handle is None:
                return None
            state = self._state(handle)
            if state.phase != "prepared":
                if state.phase == "preparation_failed":
                    self.reject_unsubmitted(handle)
                return handle
            try:
                if len(state.buffers) != len(shard):
                    raise RuntimeError("Original buffer/shard cardinality changed")
                owned = {r.key: buffer for r, buffer in zip(shard, state.buffers, strict=True)}
                groups = tuple(GroupTransfer(g, keys, tuple(owned[k] for k in keys),
                    tuple(i for i, kernel in enumerate(layout.kernels) if kernel.object_group == g))
                    for g, keys in enumerate(by_group))
                plan = TransferPlan(handle, layout, key.start, key.end,
                                    skip_first_n_tokens, tuple(blocks), groups)
                self._validate_buffers(plan)
                self.plans[handle] = plan
            except Exception as exc:
                state.errors.append(repr(exc))
                self.reject_unsubmitted(handle)
            return handle

    def _validate_buffers(self, plan):
        # Caller holds transfer and Lookup gates, then the L1 metadata lock.
        state = self._state(plan.handle)
        access = self.lookup.accesses.get(plan.handle.ticket)
        if access is None or access.phase != "delivered" or access.lease is None:
            raise RuntimeError("Original reader access missing")
        with self.lookup.l1._lock:
            lease = self.lookup.l1._lease(access.lease.handle)
            if lease is None or lease.terminal or lease.error:
                raise RuntimeError("Original lease not active")
            entries = dict(lease.entries)
            if len(state.buffers) != len(entries):
                raise RuntimeError("Original lease cardinality changed")
            for group in plan.groups:
                for key, buffer in zip(group.keys, group.buffers, strict=True):
                    entry = entries.get(key)
                    if (entry is None or self.lookup.l1._objects.get(key) is not entry
                            or entry.memory_obj is not buffer
                            or not any(b is buffer for b in state.buffers)
                            or buffer.get_size() != plan.layout.object_bytes[group.object_group]):
                        raise RuntimeError("Original source buffer identity or size changed")

    def enqueue_plan(self, handle, submit):
        """submit gets the stored plan, never caller-reconstructed buffers."""
        with self.gate:
            state = self._state(handle)
            if state is None or state.phase != "prepared":
                return False
            plan = self.plans.get(handle)
        return super().enqueue(handle, lambda buffers, callback: submit(plan, callback))

    def _validate_submission(self, state):
        try:
            plan = self.plans.get(state.handle)
            if plan is None or self.layouts.get(state.handle.ticket.worker) is not plan.layout:
                raise ValueError("Original plan registration changed")
            self._validate_buffers(plan)
        except Exception as exc:
            state.errors.append(repr(exc))
            self.reject_unsubmitted(state.handle)
            return False
        return True

    def enqueue(self, handle, submit):
        raise RuntimeError("Planned transfers require enqueue_plan")

    def _cleanup(self, state):
        closed = super()._cleanup(state)
        if closed:
            self.plans.pop(state.handle, None)
        return closed
