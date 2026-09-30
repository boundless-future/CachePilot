"""Real Request/hash/BlockPool/LMCache metadata, with no GPU or RPC."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from chunk_protection_model import Chunk, ChunkProtection
from protection_state_machine import ProtectionMachine
from token_prefix_proof import build_proof
from vllm_metadata_pool import VllmMetadataPool
from cpu_store_metadata_bridge import CpuStoreMetadataBridge

try:
    from lmcache.v1.multiprocess.token_hasher import TokenHasher
    from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPRequestMetadata, LMCacheMPRequestTracker
    from vllm.utils.hashing import get_hash_fn_by_name
    from vllm.v1.core import kv_cache_utils as utils
    from vllm.v1.core.block_pool import BlockPool
    from vllm.v1.request import Request
    from vllm import SamplingParams
except ModuleNotFoundError:
    TokenHasher = None


@unittest.skipUnless(TokenHasher, "Needs pinned LMCache/vLLM CPU contracts")
class CpuStoreMetadataBridgeTests(unittest.TestCase):
    def setUp(self):
        self.hash_fn = get_hash_fn_by_name("sha256")
        self.initial = self.hash_fn(utils.resolve_none_hash_seed(self.hash_fn))
        self.hasher = TokenHasher(256, "blake3")
        self.options = dict(vllm_hash_function=self.hash_fn,
                            vllm_initial_hash=self.initial, lmcache_hasher=self.hasher)
        with patch.object(utils, "NONE_HASH", self.initial, create=True):
            self.request = Request("r", list(range(512)), SamplingParams(max_tokens=16), None,
                block_hasher=utils.get_request_block_hasher(16, self.hash_fn))
        self.tokens = list(self.request.all_token_ids)
        self.real = BlockPool(65, enable_caching=True, hash_block_size=16)
        allocated = self.real.get_new_blocks(32)
        self.ids = [b.block_id for b in allocated]
        self.real.cache_full_blocks(self.request, allocated, 0, 32, 16, 0)
        self.real.free_blocks(allocated)
        self.pool = VllmMetadataPool(self.real)
        snapshots = self.pool.capture(self.ids)
        proof = build_proof(self.tokens, **self.options)
        self.candidates = tuple(Chunk(i, proof.chunk_hashes[i], snapshots[i*16:(i+1)*16]) for i in range(2))
        self.machine = ProtectionMachine(self.pool, 16, 2, serialize_request_ids=True)
        self.chunks = ChunkProtection(self.machine, validate_chunk=proof.validate)
        self.bridge = CpuStoreMetadataBridge(self.chunks, **self.options)
        self.session = self.machine.arrive("r")

    def prepare(self, session=None, candidates=None):
        return self.chunks.prepare(session or self.session,
            self.candidates if candidates is None else candidates, 1, 1)

    def handoff(self, decision, **kwargs):
        return self.bridge.handoff(decision.token, self.tokens,
            computed_tokens=kwargs.get("computed_tokens", 512),
            scheduled_tokens=kwargs.get("scheduled_tokens", 1))

    def test_matches_real_tracker_store_metadata_for_both_prefix_ranges(self):
        tracker = LMCacheMPRequestTracker(self.request)
        tracker.append_block_ids((self.ids,))
        for index in range(2):
            tracker.num_scheduled_tokens = (index + 1) * 256
            expected = LMCacheMPRequestMetadata.GetStoreMetadata(tracker, 256, [16])
            decision = self.prepare(candidates=self.candidates[index:])
            actual = self.handoff(decision)
            self.assertEqual(actual, expected)
            self.bridge.terminal("r")
            self.assertEqual(self.machine.saved[self.session], (index + 1) * 16)
        self.assertEqual(self.pool.allocatable, 64)

    def test_cancel_reuse_waits_for_old_terminal_then_stores_new_generation(self):
        self.handoff(self.prepare())
        self.machine.cancel(self.session)
        newer = self.machine.arrive("r")
        self.assertIsNone(self.prepare(newer).token)
        self.assertEqual(self.pool.allocatable, 48)
        self.bridge.terminal("r")
        self.assertEqual(self.machine.saved[newer], 0)
        self.handoff(self.prepare(newer))
        self.bridge.terminal("r", failed=True)
        self.assertEqual(self.machine.saved[newer], 0)
        self.assertFalse(self.pool.pins)

    def test_zero_step_rolls_back_before_handoff(self):
        self.assertIsNone(self.handoff(self.prepare(), scheduled_tokens=0))
        self.assertFalse(self.bridge.inflight)
        self.assertEqual(self.pool.allocatable, 64)

    def test_changed_tokens_or_uncomputed_tail_roll_back(self):
        decision = self.prepare()
        self.tokens[0] = 9999
        with self.assertRaises(ValueError):
            self.handoff(decision)
        self.assertFalse(self.pool.pins)
        self.tokens[0] = 0
        with self.assertRaises(ValueError):
            self.handoff(self.prepare(), computed_tokens=255)
        self.assertEqual(self.pool.allocatable, 64)

    def test_payload_does_not_alias_mutable_token_ledger(self):
        meta = self.handoff(self.prepare())
        self.tokens[0] = 9999
        self.assertEqual(meta.op.token_ids[0], 0)
        self.bridge.terminal("r")

    def test_ambiguous_dispatch_cannot_unpin_and_duplicate_handoff_fails(self):
        decision = self.prepare()
        self.handoff(decision)
        with self.assertRaises(RuntimeError):
            self.machine.submit_rejected(decision.token)
        with self.assertRaises(ValueError):
            self.handoff(decision)
        self.assertEqual(self.pool.allocatable, 48)
        self.bridge.terminal("r", failed=True)
        self.assertEqual(self.pool.allocatable, 64)

    def test_nonterminal_or_unmatched_receipt_keeps_ownership(self):
        self.handoff(self.prepare())
        for request_id, completed in [("r", 0), ("r", 2), ("other", 1)]:
            with self.assertRaises(ValueError):
                self.bridge.terminal(request_id, completed=completed)
            self.assertEqual(self.pool.allocatable, 48)
        self.bridge.terminal("r")
        with self.assertRaises(ValueError):
            self.bridge.terminal("r")


if __name__ == "__main__":
    unittest.main()
