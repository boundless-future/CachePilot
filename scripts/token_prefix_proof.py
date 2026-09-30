"""Read-only token/hash provenance for the CPU 3C chunk model.

Supports text-only, single full-attention group, no LoRA/multimodal/cache salt.
The caller must supply the actual vLLM hash callable and initial parent hash;
this helper never changes vLLM's global NONE_HASH or invents a runtime seed.
It validates metadata identity, not the contents of CUDA KV tensors.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenPrefixProof:
    chunk_hashes: tuple[str, ...]
    physical_hashes: tuple[tuple[str, ...], ...]

    def validate(self, chunk):
        if type(chunk.index) is not int or not 0 <= chunk.index < len(self.chunk_hashes):
            return False
        return (chunk.prefix_hash == self.chunk_hashes[chunk.index]
                and tuple(b.prefix_hash for b in chunk.blocks) == self.physical_hashes[chunk.index])


def build_proof(token_ids, *, vllm_hash_function, vllm_initial_hash, lmcache_hasher,
                block_tokens=16, chunk_tokens=256, group_id=0,
                cache_salt="", has_extra_keys=False):
    """Compute both real rolling hash chains from one immutable token ledger."""
    from vllm.v1.core.kv_cache_utils import hash_block_tokens, make_block_hash_with_group_id

    if cache_salt or has_extra_keys or group_id != 0:
        raise ValueError("Only unsalted text-only group 0 is supported")
    if any(type(v) is not int or v <= 0 for v in (block_tokens, chunk_tokens)) or chunk_tokens % block_tokens:
        raise ValueError("Invalid block/chunk geometry")
    if lmcache_hasher.chunk_size != chunk_tokens:
        raise ValueError("Hasher geometry mismatch")
    if not isinstance(vllm_initial_hash, bytes) or not vllm_initial_hash:
        raise ValueError("Explicit runtime vLLM initial hash required")
    tokens = tuple(token_ids)
    if any(type(t) is not int or t < 0 for t in tokens):
        raise ValueError("Invalid token ledger")
    complete = len(tokens) // chunk_tokens * chunk_tokens
    lm_hashes = lmcache_hasher.compute_chunk_hashes(list(tokens), end=complete)
    if any(not isinstance(h, bytes) for h in lm_hashes):
        raise ValueError("Unsupported non-byte LMCache hash")
    parent = vllm_initial_hash
    hashes = []
    for offset in range(0, complete, block_tokens):
        parent = hash_block_tokens(vllm_hash_function, parent,
                                   tokens[offset:offset+block_tokens])
        hashes.append(make_block_hash_with_group_id(parent, group_id).hex())
    count = chunk_tokens // block_tokens
    return TokenPrefixProof(tuple(h.hex() for h in lm_hashes),
        tuple(tuple(hashes[i:i+count]) for i in range(0, len(hashes), count)))
