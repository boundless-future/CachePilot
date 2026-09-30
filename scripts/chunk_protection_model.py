"""3C physical-block/chunk mapping model; no actual vLLM or LMCache hooks.

The wrapped owner machine counts physical blocks, while this adapter exposes
STORE prefix lengths in complete LMCache chunks. Only full chunks can pin.
"""
from dataclasses import dataclass

from protection_state_machine import Block, Decision, ProtectionMachine


@dataclass(frozen=True)
class Chunk:
    index: int
    prefix_hash: str
    blocks: tuple[Block, ...]


class ChunkProtection:
    def __init__(self, machine: ProtectionMachine, block_tokens=16, chunk_tokens=256,
                 *, validate_chunk):
        if any(type(v) is not int or v <= 0 for v in (block_tokens, chunk_tokens)):
            raise ValueError("Token sizes must be positive integers")
        if chunk_tokens % block_tokens:
            raise ValueError("Chunk must contain a whole number of physical blocks")
        self.machine = machine
        self.blocks_per_chunk = chunk_tokens // block_tokens
        # LMCache's chunk hash is not equal to each vLLM block hash. The
        # supplied oracle must attest token order and chunk identity; this
        # model only validates the physical snapshots and geometry itself.
        if not callable(validate_chunk):
            raise ValueError("An explicit chunk provenance oracle is required")
        self.validate_chunk = validate_chunk

    def prepare(self, session, chunks, demand_blocks, scheduled_tokens):
        if any(type(v) is not int or v < 0 for v in (demand_blocks, scheduled_tokens)):
            raise ValueError("Invalid demand or scheduled token count")
        if self.machine.current.get(session.request_id) != session:
            return Decision(None, "stale_generation")
        start = self.machine.saved[session]
        if start % self.blocks_per_chunk:
            raise RuntimeError("Stored physical prefix is not chunk-aligned")
        start_chunk = start // self.blocks_per_chunk
        chosen = []
        new = set()
        for offset, chunk in enumerate(chunks):
            if (chunk.index != start_chunk + offset or not chunk.prefix_hash
                    or len(chunk.blocks) != self.blocks_per_chunk):
                break
            if not self.validate_chunk(chunk):
                break
            if not all(self.machine.pool.matches(b) for b in chunk.blocks):
                break
            proposed = new | {b for b in chunk.blocks if b not in self.machine.pool.pins}
            if len(self.machine.pool.pins) + len(proposed) > self.machine.max_pins:
                break
            if self.machine.pool.allocatable - len(proposed) < demand_blocks + self.machine.reserve:
                break
            new = proposed
            chosen.extend(chunk.blocks)
        # Called synchronously on the owner thread; the oracle must not mutate
        # between selection and pin. Under the same values, prepare cannot
        # truncate in the middle of a chunk selected above.
        decision = self.machine.prepare(session, tuple(chosen), start,
                                        demand_blocks, scheduled_tokens)
        if decision.token is not None and decision.protected % self.blocks_per_chunk:
            self.machine.submit_rejected(decision.token)
            raise RuntimeError("Chunk changed between validation and pin")
        return decision

    def receipt(self, token, success, stored_chunks=0):
        if type(stored_chunks) is not int or stored_chunks < 0:
            raise ValueError("Stored chunk count must be a nonnegative integer")
        return self.machine.receipt(token, success, stored_chunks * self.blocks_per_chunk)
