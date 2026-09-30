from dataclasses import dataclass
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from preemption_probe_range import whole_loaded_chunks


@dataclass
class Op:
    start: int
    end: int
    block_ids: list
    skip_first_n_tokens: int = 0


class ProbeRangeTests(unittest.TestCase):
    def trim(self, op):
        return whole_loaded_chunks(op, block_tokens=16, chunk_tokens=256)

    def test_partial_apc_chunk_is_excluded_without_modifying_original(self):
        op = Op(256, 1024, [list(range(48))], 64)
        result = self.trim(op)
        self.assertEqual((result.start, result.end), (512, 1024))
        self.assertEqual(result.block_ids, [list(range(16, 48))])
        self.assertEqual(result.skip_first_n_tokens, 0)
        self.assertEqual(op.start, 256)
        self.assertEqual(len(op.block_ids[0]), 48)

    def test_no_skip_keeps_every_chunk_and_full_skip_gives_no_evidence(self):
        self.assertEqual(self.trim(Op(0, 512, [list(range(32))])).start, 0)
        self.assertIsNone(self.trim(Op(0, 256, [list(range(16))], 16)))

    def test_invalid_or_noncontiguous_geometry_fails(self):
        for op in [Op(16, 512, [list(range(31))]), Op(0, 512, [[1]]),
                   Op(0, 256, [list(range(16))], -1)]:
            with self.assertRaises(ValueError):
                self.trim(op)
