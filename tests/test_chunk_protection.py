from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from chunk_protection_model import Chunk, ChunkProtection
from protection_state_machine import Block, FakeBlockPool, ProtectionMachine


class ChunkProtectionTests(unittest.TestCase):
    def setUp(self):
        self.chunks = tuple(Chunk(i, f"chunk-hash-{i}", tuple(
            Block(i * 16 + j, 1, f"block-hash-{i}-{j}") for j in range(16))) for i in range(3))
        self.pool = FakeBlockPool([b for c in self.chunks for b in c.blocks], 64)
        self.machine = ProtectionMachine(self.pool, 24, 2, 4)
        self.adapter = ChunkProtection(self.machine, validate_chunk=lambda c: c in self.chunks)
        self.session = self.machine.arrive("r")

    def test_budget_never_pins_a_partial_chunk(self):
        d = self.adapter.prepare(self.session, self.chunks, 16, 1)
        self.assertEqual(d.protected, 16)  # 24-block budget cannot save 1.5 chunks.
        action = self.machine.submit(d.token)
        self.assertEqual(len(action.blocks), 16)
        self.adapter.receipt(d.token, True, 1)
        self.assertEqual(self.machine.saved[self.session], 16)
        d = self.adapter.prepare(self.session, self.chunks[1:], 16, 1)
        self.assertEqual(d.protected, 16)
        self.machine.cancel(self.session)
        self.assertFalse(self.pool.pins)

    def test_one_stale_block_invalidates_whole_chunk_and_later_prefix(self):
        self.pool.overwrite(Block(8, 2, "reallocated"))
        d = self.adapter.prepare(self.session, self.chunks, 16, 1)
        self.assertIsNone(d.token)
        self.assertFalse(self.pool.pins)

    def test_no_skip_over_missing_chunk(self):
        d = self.adapter.prepare(self.session, self.chunks[1:], 16, 1)
        self.assertIsNone(d.token)

    def test_receipt_cannot_claim_more_chunks_than_submitted(self):
        d = self.adapter.prepare(self.session, self.chunks, 16, 1)
        self.machine.submit(d.token)
        with self.assertRaises(ValueError):
            self.adapter.receipt(d.token, True, 2)
        self.assertEqual(len(self.pool.pins), 16)
        self.adapter.receipt(d.token, False)
        self.assertFalse(self.pool.pins)

    def test_invalid_geometry_and_incomplete_tail(self):
        with self.assertRaises(ValueError):
            ChunkProtection(self.machine, 16, 255, validate_chunk=lambda c: True)
        incomplete = Chunk(0, "chunk-hash-0", self.chunks[0].blocks[:15])
        self.assertIsNone(self.adapter.prepare(self.session, [incomplete], 16, 1).token)

    def test_demand_and_reserve_must_fit_alongside_full_chunk(self):
        self.assertIsNone(self.adapter.prepare(self.session, self.chunks, 45, 1).token)
        self.assertFalse(self.pool.pins)

    def test_distinct_block_hashes_require_explicit_chunk_provenance(self):
        bad = Chunk(0, "different-token-prefix", self.chunks[0].blocks)
        self.assertIsNone(self.adapter.prepare(self.session, [bad], 16, 1).token)
        self.assertFalse(self.pool.pins)
        with self.assertRaises(ValueError):
            ChunkProtection(self.machine, validate_chunk=None)


if __name__ == "__main__":
    unittest.main()
