"""Use real vLLM queue/refcounts without a model or CUDA allocation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from protection_state_machine import ProtectionMachine
from vllm_metadata_pool import VllmMetadataPool
from chunk_protection_model import Chunk, ChunkProtection

try:
    from vllm.v1.core.block_pool import BlockPool
    from vllm.v1.core.kv_cache_utils import BlockHash, make_block_hash_with_group_id
except ModuleNotFoundError:
    BlockPool = None


@unittest.skipUnless(BlockPool is not None, "Needs pinned vLLM environment")
class VllmMetadataPoolTests(unittest.TestCase):
    def setUp(self):
        self.real = BlockPool(65, enable_caching=True, hash_block_size=16)
        cached = self.real.get_new_blocks(32)
        for block in cached:
            self.real._insert_block_hash(make_block_hash_with_group_id(
                BlockHash(block.block_id.to_bytes(32, "big")), 0), block, block.block_id * 16)
        self.real.free_blocks(cached)
        self.pool = VllmMetadataPool(self.real)
        self.blocks = self.pool.capture([b.block_id for b in cached])
        self.machine = ProtectionMachine(self.pool, 16, 2, 4)
        self.session = self.machine.arrive("r")

    def test_null_block_is_not_free_or_protectable(self):
        self.assertEqual(self.pool.allocatable, 64)
        self.assertEqual(self.pool.capture([0, 1]), ())

    def test_real_allocation_preserves_pin_until_terminal_receipt(self):
        decision = self.machine.prepare(self.session, self.blocks, 0, 44, 1)
        self.assertEqual(decision.protected, 16)
        action = self.machine.submit(decision.token)
        allocated = self.real.get_new_blocks(44)
        self.assertFalse({b.block_id for b in allocated} & {b.block_id for b in action.blocks})
        self.assertEqual(self.pool.allocatable, 4)
        self.machine.cancel(self.session)
        self.machine.assert_invariants()
        self.machine.receipt(action.token, False)
        self.real.free_blocks(allocated)
        self.assertEqual(self.pool.allocatable, 64)
        self.assertFalse(self.pool.pins)
        self.assertTrue(all(b.ref_cnt == 0 for b in self.real.blocks[1:]))

    def test_two_generations_share_physical_pin_refcounts(self):
        first = self.machine.prepare(self.session, self.blocks[:16], 0, 1, 1)
        self.machine.submit(first.token)
        new = self.machine.arrive("r")
        second = self.machine.prepare(new, self.blocks[:16], 0, 1, 1)
        self.assertEqual(second.protected, 16)
        self.machine.submit(second.token)
        self.assertEqual(self.pool.allocatable, 48)
        self.assertEqual(self.real.blocks[1].ref_cnt, 2)
        self.machine.receipt(first.token, True, 16)
        self.assertEqual(self.machine.saved[new], 0)
        self.assertEqual(self.real.blocks[1].ref_cnt, 1)
        self.machine.receipt(second.token, True, 16)
        self.assertEqual(self.pool.allocatable, 64)

    def test_stale_hash_and_active_blocks_stop_contiguous_selection(self):
        # Consume all free blocks to force genuine cache eviction.
        allocated = self.real.get_new_blocks(64)
        decision = self.machine.prepare(self.session, self.blocks, 0, 0, 1)
        self.assertIsNone(decision.token)
        self.assertFalse(self.pool.pins)
        self.real.free_blocks(allocated)

    def test_external_owner_is_rejected(self):
        physical = self.real.blocks[1]
        self.real.touch([physical])
        self.assertEqual(self.pool.capture([1, 2]), ())
        self.real.free_blocks([physical])
        self.assertEqual(len(self.pool.capture([1])), 1)

    def test_partial_block_hash_is_rejected(self):
        physical = self.real.blocks[1]
        self.real._maybe_evict_cached_block(physical)
        self.real._insert_block_hash(make_block_hash_with_group_id(
            BlockHash(b"partial-prefix"), 0), physical, 8)
        self.assertEqual(self.pool.capture([1, 2]), ())
        self.assertFalse(self.pool.matches(self.blocks[0]))

    def test_default_allocate_reuses_unprotected_cached_blocks(self):
        allocated = self.real.get_new_blocks(49)
        self.assertTrue(any(not self.pool.matches(b) for b in self.blocks[:16]))
        self.real.free_blocks(allocated)
        self.assertEqual(self.pool.allocatable, 64)

    def test_chunk_budget_uses_sixteen_distinct_real_block_hashes(self):
        chunks = tuple(Chunk(i, f"synthetic-token-chunk-{i}", self.blocks[i*16:(i+1)*16])
                       for i in range(2))
        adapter = ChunkProtection(self.machine, validate_chunk=lambda c: c in chunks)
        decision = adapter.prepare(self.session, chunks, 44, 1)
        self.assertEqual(decision.protected, 16)
        self.assertEqual(len({b.prefix_hash for b in chunks[0].blocks}), 16)
        self.machine.submit(decision.token)
        adapter.receipt(decision.token, True, 1)
        self.assertEqual(self.machine.saved[self.session], 16)
        self.assertEqual(self.pool.allocatable, 64)


if __name__ == "__main__":
    unittest.main()
