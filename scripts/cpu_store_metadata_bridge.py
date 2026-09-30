"""CPU-only handoff contract using the installed LMCache metadata classes.

This does not send an RPC, install a connector, or aggregate real workers.
The harness supplies a terminal result for one worker. Its request-id wire
view assumes exactly-once, ordered delivery within one engine incarnation;
it cannot detect an old duplicated receipt after the ID has been reused.
"""
from chunk_protection_model import Chunk
from protection_state_machine import Phase
from token_prefix_proof import build_proof


class CpuStoreMetadataBridge:
    def __init__(self, chunks, *, vllm_hash_function, vllm_initial_hash,
                 lmcache_hasher, block_tokens=16, chunk_tokens=256):
        self.chunks, self.machine = chunks, chunks.machine
        if not self.machine.serialize_request_ids:
            raise ValueError("Request-id wire view requires serialized generations")
        if (type(block_tokens) is not int or type(chunk_tokens) is not int
                or block_tokens <= 0 or chunk_tokens <= 0
                or chunk_tokens % block_tokens
                or chunks.blocks_per_chunk != chunk_tokens // block_tokens):
            raise ValueError("Metadata and protection geometry differ")
        self.block_tokens, self.chunk_tokens = block_tokens, chunk_tokens
        self.proof_options = dict(vllm_hash_function=vllm_hash_function,
            vllm_initial_hash=vllm_initial_hash, lmcache_hasher=lmcache_hasher,
            block_tokens=block_tokens, chunk_tokens=chunk_tokens)
        self.inflight = {}

    def handoff(self, token, token_ids, *, computed_tokens, scheduled_tokens):
        """Build one real STORE metadata object and mark local ownership sent.

Validation errors and a zero-token step occur before handoff and roll back
PREPARED pins. Once returned, caller-side errors are ambiguous dispatch and
must not call submit_rejected; pins remain until a terminal result.
"""
        batch = self.machine.batches.get(token)
        if batch is None or batch.phase is not Phase.PREPARED:
            raise ValueError("Handoff requires a live prepared batch")
        try:
            if any(type(v) is not int or v < 0 for v in (computed_tokens, scheduled_tokens)):
                raise ValueError("Invalid token counts")
            if scheduled_tokens == 0:
                self.machine.submit_rejected(token)
                return None
            rid = batch.session.request_id
            if rid in self.inflight:
                raise RuntimeError("One STORE per raw request-id may be in flight")
            if self.machine.current.get(rid) != batch.session:
                raise ValueError("Stale request generation")
            size = self.chunks.blocks_per_chunk
            if not batch.blocks or batch.start % size or len(batch.blocks) % size:
                raise ValueError("STORE must contain complete chunks")
            tokens = tuple(token_ids)
            start = batch.start * self.block_tokens
            end = start + len(batch.blocks) * self.block_tokens
            if end > min(len(tokens), computed_tokens):
                raise ValueError("STORE exceeds computed token ledger")
            proof = build_proof(tokens, **self.proof_options)
            for offset in range(0, len(batch.blocks), size):
                index = (batch.start + offset) // size
                blocks = batch.blocks[offset:offset + size]
                chunk = Chunk(index, proof.chunk_hashes[index], blocks)
                if not proof.validate(chunk) or not all(self.machine.pool.matches(b) for b in blocks):
                    raise ValueError("Token/hash/physical identity changed before handoff")
            from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPRequestMetadata, LoadStoreOp
            meta = LMCacheMPRequestMetadata(rid, "STORE", LoadStoreOp(
                token_ids=list(tokens), block_ids=[[b.block_id for b in batch.blocks]],
                start=start, end=end))
        except Exception:
            self.machine.submit_rejected(token)
            raise
        action = self.machine.submit(token)
        assert action is not None
        self.inflight[rid] = (token, len(batch.blocks) // size)
        return meta

    def terminal(self, request_id, *, completed=1, failed=False):
        """Apply an already terminal single-worker result, not a timeout."""
        if type(completed) is not int or completed != 1 or type(failed) is not bool:
            raise ValueError("Only one terminal worker receipt is supported")
        if request_id not in self.inflight:
            raise ValueError("No matching in-flight request; do not guess a generation")
        token, count = self.inflight[request_id]
        if token not in self.machine.batches:
            raise RuntimeError("Ownership was changed outside this bridge")
        self.chunks.receipt(token, not failed, 0 if failed else count)
        del self.inflight[request_id]
