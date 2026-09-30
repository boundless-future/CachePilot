"""CPU contract bridge to a real vLLM BlockPool, never installed in a server.

Only fully hashed, idle single-group blocks may be selected. This exercises
real free-queue/refcount methods, but owns no CUDA tensors and predicts no
demand. Snapshot versions belong to this bridge, not to vLLM's allocator.
"""
from protection_state_machine import Block


class VllmMetadataPool:
    def __init__(self, pool, block_tokens=16):
        self.pool = pool
        self.block_tokens = block_tokens
        self.pins = {}
        self.pin_events = []
        self.unpin_events = []
        self._snapshots = {}
        self._version = 0

    @property
    def allocatable(self):
        return self.pool.get_num_free_blocks()

    def _full_hash(self, physical):
        # vLLM stores the cumulative prefix token count at this boundary,
        # not the number of tokens within this one physical block.
        count = physical.block_hash_num_tokens
        return type(count) is int and count > 0 and count % self.block_tokens == 0

    def capture(self, block_ids):
        """Caller supplies prefix order; this does not prove token provenance."""
        if len(set(block_ids)) != len(block_ids):
            raise ValueError("Duplicate physical block")
        captured = []
        for block_id in block_ids:
            if type(block_id) is not int or not 0 <= block_id < len(self.pool.blocks):
                raise ValueError("Invalid physical block")
            physical = self.pool.blocks[block_id]
            if (physical.is_null or physical.pool is not self.pool
                    or physical.block_hash is None
                    or not self._full_hash(physical)):
                break
            digest = physical.block_hash.hex()
            old = self._snapshots.get(block_id)
            if old is None or old.prefix_hash != digest:
                self._version += 1
                old = Block(block_id, self._version, digest)
                self._snapshots[block_id] = old
            if not self.matches(old):
                break
            captured.append(old)
        return tuple(captured)

    def matches(self, block):
        if self._snapshots.get(block.block_id) != block:
            return False
        physical = self.pool.blocks[block.block_id]
        return (not physical.is_null and physical.pool is self.pool
                and physical.block_hash is not None
                and physical.block_hash.hex() == block.prefix_hash
                and self._full_hash(physical)
                and physical.ref_cnt == self.pins.get(block, 0))

    def pin(self, block):
        if not self.matches(block):
            raise ValueError("Changed hash, partial block, or external owner")
        # This is only a CPU contract harness on one owner thread. Do not
        # install it as an allocator callback or run alongside a scheduler.
        self.pool.touch([self.pool.blocks[block.block_id]])
        self.pins[block] = self.pins.get(block, 0) + 1
        self.pin_events.append(block)

    def unpin(self, block):
        count = self.pins[block]
        if not self.matches(block):
            raise RuntimeError("Protected block metadata changed")
        self.pool.free_blocks([self.pool.blocks[block.block_id]])
        if count == 1:
            del self.pins[block]
        else:
            self.pins[block] = count - 1
        self.unpin_events.append(block)
