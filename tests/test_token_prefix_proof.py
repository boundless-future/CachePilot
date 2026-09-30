from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from token_prefix_proof import build_proof
from chunk_protection_model import Chunk
from protection_state_machine import Block

try:
    from lmcache.v1.multiprocess.token_hasher import TokenHasher
    from vllm.utils.hashing import get_hash_fn_by_name
except ModuleNotFoundError:
    TokenHasher = None


@unittest.skipUnless(TokenHasher, "Needs real LMCache and vLLM hash functions")
class TokenPrefixProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lm = TokenHasher(256, "blake3")
        cls.hash_fn = staticmethod(get_hash_fn_by_name("sha256"))

    def proof(self, tokens, **kwargs):
        return build_proof(tokens, vllm_hash_function=self.hash_fn,
            vllm_initial_hash=b"test-explicit-parent", lmcache_hasher=self.lm, **kwargs)

    def chunk(self, proof, index=0):
        return Chunk(index, proof.chunk_hashes[index], tuple(Block(i+1, 1, h)
                     for i, h in enumerate(proof.physical_hashes[index])))

    def test_two_real_hash_chains_prove_ordered_chunk(self):
        proof = self.proof(range(512))
        self.assertTrue(proof.validate(self.chunk(proof)))
        self.assertEqual(len(set(proof.physical_hashes[0])), 16)
        self.assertNotIn(proof.chunk_hashes[0], proof.physical_hashes[0])

    def test_changed_prefix_invalidates_later_chunk_chain(self):
        original = self.proof(range(512))
        tokens = list(range(512))
        tokens[0] = 1000
        changed = self.proof(tokens)
        self.assertFalse(changed.validate(self.chunk(original, 1)))
        self.assertNotEqual(changed.chunk_hashes[1], original.chunk_hashes[1])

    def test_reordered_physical_hashes_fail(self):
        proof = self.proof(range(256))
        chunk = self.chunk(proof)
        self.assertFalse(proof.validate(Chunk(0, chunk.prefix_hash, tuple(reversed(chunk.blocks)))))

    def test_incomplete_tail_never_creates_a_protectable_chunk(self):
        proof = self.proof(range(511))
        self.assertEqual(len(proof.chunk_hashes), 1)
        self.assertFalse(proof.validate(Chunk(1, "tail", ())))

    def test_unsupported_identity_context_is_rejected(self):
        for options in (dict(cache_salt="tenant"), dict(has_extra_keys=True),
                        dict(group_id=1), dict(chunk_tokens=512)):
            with self.assertRaises(ValueError):
                self.proof(range(512), **options)

    def test_real_request_decode_append_and_pool_metadata_match_proof(self):
        from vllm import SamplingParams
        from vllm.v1.request import Request
        from vllm.v1.core import kv_cache_utils as utils
        from vllm.v1.core.block_pool import BlockPool
        from vllm_metadata_pool import VllmMetadataPool
        from protection_state_machine import ProtectionMachine
        from chunk_protection_model import ChunkProtection

        initial = self.hash_fn(utils.resolve_none_hash_seed(self.hash_fn))
        # Local test patch restores any original global seed after Request's
        # public hasher runs. Production build_proof never mutates globals.
        with patch.object(utils, "NONE_HASH", initial, create=True):
            request = Request("tokens", list(range(256)), SamplingParams(max_tokens=256), None,
                block_hasher=utils.get_request_block_hasher(16, self.hash_fn))
            request.append_output_token_ids(list(range(256, 512)))
        real = BlockPool(65, enable_caching=True, hash_block_size=16)
        allocated = real.get_new_blocks(32)
        real.cache_full_blocks(request, allocated, 0, 32, 16, 0)
        real.free_blocks(allocated)
        bridge = VllmMetadataPool(real)
        snapshots = bridge.capture([b.block_id for b in allocated])
        self.assertEqual(len(snapshots), 32)
        proof = build_proof(request.all_token_ids, vllm_hash_function=self.hash_fn,
                            vllm_initial_hash=initial, lmcache_hasher=self.lm)
        chunks = tuple(Chunk(i, proof.chunk_hashes[i], snapshots[i*16:(i+1)*16]) for i in range(2))
        self.assertTrue(all(proof.validate(c) for c in chunks))
        machine = ProtectionMachine(bridge, 16, 1, 4, serialize_request_ids=True)
        adapter = ChunkProtection(machine, validate_chunk=proof.validate)
        decision = adapter.prepare(machine.arrive("tokens"), chunks, 44, 1)
        self.assertEqual(decision.protected, 16)
        machine.submit(decision.token)
        adapter.receipt(decision.token, True, 1)
        self.assertEqual(bridge.allocatable, 64)


if __name__ == "__main__":
    unittest.main()
