"""Stage 3C executable reference model, deliberately independent of GPU APIs.

The owner counts abstract physical blocks; chunk_protection_model adds complete
chunk grouping. Real hash validation, pin hooks, and demand selection still
require an adapter. The simple demo uses one block per chunk explicitly.
All operations run on one scheduler owner thread. No performance claims.
"""
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Session:
    request_id: str
    generation: int


@dataclass(frozen=True)
class Block:
    block_id: int
    version: int
    prefix_hash: str


class Phase(str, Enum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"


@dataclass
class Batch:
    token: int
    session: Session
    start: int
    blocks: tuple[Block, ...]
    phase: Phase = Phase.PREPARED
    orphaned: bool = False


@dataclass(frozen=True)
class Decision:
    token: int | None
    reason: str
    protected: int = 0


@dataclass(frozen=True)
class StoreAction:
    token: int
    session: Session
    start: int
    blocks: tuple[Block, ...]


class FakeBlockPool:
    """Allocator oracle: pinned blocks cannot be overwritten or reallocated."""
    def __init__(self, blocks, free_capacity):
        self.blocks = {b.block_id: b for b in blocks}
        if type(free_capacity) is not int or free_capacity < 0:
            raise ValueError("Capacity must be a nonnegative integer")
        if any(type(i) is not int or not 0 <= i < free_capacity for i in self.blocks):
            raise ValueError("Block ids must fit capacity")
        self.free_capacity = free_capacity
        self.active_allocations = set()
        self.pins = {}
        self.pin_events = []
        self.unpin_events = []

    @property
    def allocatable(self):
        return self.free_capacity - len(self.pins) - len(self.active_allocations)

    def matches(self, block):
        return (block.block_id not in self.active_allocations
                and self.blocks.get(block.block_id) == block)

    def pin(self, block):
        if not self.matches(block):
            raise ValueError("Stale block identity")
        self.pins[block] = self.pins.get(block, 0) + 1
        self.pin_events.append(block)

    def unpin(self, block):
        count = self.pins[block]
        if count == 1:
            del self.pins[block]
        else:
            self.pins[block] = count - 1
        self.unpin_events.append(block)

    def overwrite(self, block):
        if not 0 <= block.block_id < self.free_capacity:
            raise ValueError("Block id outside pool")
        if block.block_id in self.active_allocations:
            raise RuntimeError("Cannot overwrite an active allocation")
        if any(b.block_id == block.block_id for b in self.pins):
            raise RuntimeError("Cannot overwrite a pinned block")
        self.blocks[block.block_id] = block

    def allocate(self, count):
        """Deterministic free-queue pressure, not a real vLLM allocator."""
        if type(count) is not int or count < 0 or count > self.allocatable:
            raise ValueError("Insufficient allocatable blocks")
        pinned_ids = {b.block_id for b in self.pins}
        ids = tuple(i for i in range(self.free_capacity)
                    if i not in pinned_ids and i not in self.active_allocations)[:count]
        for block_id in ids:
            self.blocks.pop(block_id, None)
            self.active_allocations.add(block_id)
        return ids

    def release_allocations(self, block_ids):
        if len(set(block_ids)) != len(block_ids) or not set(block_ids) <= self.active_allocations:
            raise ValueError("Unknown or duplicate active allocation")
        self.active_allocations.difference_update(block_ids)


class ProtectionMachine:
    """Budgeted prefix STORE lifecycle; zero-token steps never acquire pins.

Demand is a supplied oracle for this model, not the project's noisy lookup
signal. Fallback always leaves default scheduling available; this model never
blocks admission. Cancellation or timeout cannot unpin a submitted batch.
"""
    def __init__(self, pool, max_pins, max_inflight, reserve_blocks=0,
                 *, serialize_request_ids=False):
        for value in (max_pins, max_inflight, reserve_blocks):
            if type(value) is not int or value < 0:
                raise ValueError("Budgets must be nonnegative integers")
        self.pool = pool
        self.max_pins, self.max_inflight, self.reserve = max_pins, max_inflight, reserve_blocks
        self.serialize_request_ids = serialize_request_ids
        self.current = {}
        self.saved = {}
        self.batches = {}
        self._generation = 0
        self._token = 0

    def arrive(self, request_id):
        old = self.current.get(request_id)
        if old is not None:
            self.cancel(old)
        self._generation += 1
        session = Session(request_id, self._generation)
        self.current[request_id] = session
        self.saved[session] = 0
        return session

    def prepare(self, session, blocks, start, demand, scheduled_tokens):
        if any(type(v) is not int or v < 0 for v in (start, demand, scheduled_tokens)):
            raise ValueError("Counts must be nonnegative integers")
        if self.current.get(session.request_id) != session:
            return Decision(None, "stale_generation")
        if scheduled_tokens == 0:
            return Decision(None, "zero_token_step")
        if self.serialize_request_ids and any(
                b.session.request_id == session.request_id for b in self.batches.values()):
            return Decision(None, "request_id_inflight")
        if any(b.session == session for b in self.batches.values()):
            return Decision(None, "session_inflight")
        if len(self.batches) >= self.max_inflight:
            return Decision(None, "inflight_budget")
        if start != self.saved[session]:
            return Decision(None, "prefix_gap")
        if len({b.block_id for b in blocks}) != len(blocks):
            raise ValueError("Repeated physical block in prefix")
        if any(type(b.version) is not int or b.version < 0 or not b.prefix_hash for b in blocks):
            raise ValueError("Invalid block identity")

        # Select only a contiguous valid prefix and stop at the first failure.
        # Shared physical chunks consume one capacity unit but retain one pin
        # reference per batch. There is no speculative wait for more budget.
        chosen = []
        new_pins = 0
        for block in blocks:
            if not self.pool.matches(block):
                break
            added = int(block not in self.pool.pins)
            if len(self.pool.pins) + new_pins + added > self.max_pins:
                break
            if self.pool.allocatable - new_pins - added < demand + self.reserve:
                break
            chosen.append(block)
            new_pins += added
        if not chosen:
            return Decision(None, "no_safe_prefix")

        pinned = []
        try:
            for block in chosen:
                self.pool.pin(block)
                pinned.append(block)
        except Exception:
            for block in reversed(pinned):
                self.pool.unpin(block)
            raise
        self._token += 1
        batch = Batch(self._token, session, start, tuple(chosen))
        self.batches[batch.token] = batch
        return Decision(batch.token, "prepared", len(chosen))

    def submit(self, token):
        batch = self.batches.get(token)
        if batch is None or batch.phase is Phase.SUBMITTED:
            return None
        batch.phase = Phase.SUBMITTED
        return StoreAction(batch.token, batch.session, batch.start, batch.blocks)

    def submit_rejected(self, token):
        """Only use before dispatch or after a proven synchronous rejection."""
        batch = self.batches.get(token)
        if batch is None:
            return False
        if batch.phase is not Phase.PREPARED:
            raise RuntimeError("Submitted work needs a terminal receipt")
        self._release(batch)
        return True

    def receipt(self, token, success, stored_prefix=0):
        batch = self.batches.get(token)
        if batch is None:
            return False  # Duplicate/old token cannot address a new generation.
        if batch.phase is not Phase.SUBMITTED:
            raise RuntimeError("Receipt before dispatch")
        if type(success) is not bool or type(stored_prefix) is not int:
            raise ValueError("Invalid receipt types")
        if not 0 <= stored_prefix <= len(batch.blocks) or (not success and stored_prefix):
            raise ValueError("Invalid stored prefix")
        if success and not batch.orphaned and self.current.get(batch.session.request_id) == batch.session:
            self.saved[batch.session] = batch.start + stored_prefix
        self._release(batch)
        return True

    def cancel(self, session):
        if self.current.get(session.request_id) == session:
            del self.current[session.request_id]
        for batch in list(self.batches.values()):
            if batch.session != session:
                continue
            if batch.phase is Phase.PREPARED:
                self._release(batch)
            else:
                batch.orphaned = True
        self.saved.pop(session, None)

    def reset(self):
        for session in list(self.current.values()):
            self.cancel(session)

    def pending(self):
        return [(b.token, b.phase.value, b.orphaned) for b in self.batches.values()]

    def _release(self, batch):
        for block in batch.blocks:
            self.pool.unpin(block)
        del self.batches[batch.token]

    def assert_invariants(self):
        expected = {}
        for batch in self.batches.values():
            for block in batch.blocks:
                assert self.pool.matches(block)
                expected[block] = expected.get(block, 0) + 1
            if batch.orphaned:
                assert batch.phase is Phase.SUBMITTED
        assert self.pool.pins == expected
        assert len(expected) <= self.max_pins
        assert len(self.batches) <= self.max_inflight
        assert self.pool.allocatable >= 0
