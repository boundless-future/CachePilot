"""CPU contract candidate: own one reference per block, including shared heads.

No model-service binding yet. Existing production/diagnostic helpers unchanged.
Conservative capacity reservation and full token/hash proof remain unchanged.
"""
from cachepilot.runtime.protection import conservative_reserve, Prepared
from cachepilot.runtime.candidates import ranked_candidates
from cachepilot.integration.lmcache.protection import GenerationBoundedProtection
from cachepilot.runtime.handoff import describe
from cachepilot.runtime.prefix_proof import build_proof


class SharedPrefixProtection(GenerationBoundedProtection):
    def bind(self, kv_manager):
        raise RuntimeError('Shared prefix candidate requires independent service admission')

    def prepare(self, scheduler):
        self.manager._healthy()
        from vllm.v1.core import kv_cache_utils
        if self.prepared:
            raise RuntimeError("Unsettled PREPARED batch from previous step")
        reserve = self.admission_policy.reserve(scheduler)
        if reserve is None:
            return
        used = sum(len(p.blocks) for p in self.inflight.values())
        left = self.budget - used
        free = self.pool.get_num_free_blocks()
        if free < reserve + 16:
            for rid, pending in self.policy._pending.items():
                if (rid in scheduler.requests or not self.manager._requests.is_finished(rid)
                        or self.manager._requests.has_in_flight(rid) or rid in self.inflight):
                    continue
                meta = pending[0].store_metadata
                op = meta.op
                if (op.start == 0 and op.end >= 256 and op.end % 256 == 0 and not op.skip_first_n_tokens
                        and not meta.cache_salt and len(op.block_ids) == 1
                        and len(op.block_ids[0]) == (op.end - op.start) // 16
                        and left >= 16):
                    self._capacity_fallback(meta, min(len(op.block_ids[0]), left), free, reserve, left, used)
                    break
            return
        # Use actual full-footprint pressure only to rank imminent candidates.
        all_ranks = self.policy._free_queue_ranks(free)
        ranks = {bid: rank for bid, rank in all_ranks.items() if rank < reserve}
        candidates = ranked_candidates(self.policy._pending, scheduler.requests,
            self.manager._requests, self.inflight, ranks)
        for _, rid, candidate in candidates:
            meta = candidate.store_metadata
            op = meta.op
            if (op.start != 0 or op.end < 256 or op.end % 256 or op.skip_first_n_tokens
                    or meta.cache_salt or len(op.block_ids) != 1):
                continue
            ids = list(op.block_ids[0])
            if len(ids) != (op.end - op.start) // 16 or len(set(ids)) != len(ids):
                continue
            if left < 16:
                identity = describe(meta)
                observed = getattr(self, "_inflight_budget_observed", set())
                if identity["wire_id"] not in observed:
                    observed.add(identity["wire_id"])
                    self._inflight_budget_observed = observed
                    self.record("protection_fallback", reason="inflight_budget",
                        **identity, required_blocks=len(ids), available_blocks=left,
                        budget_blocks=self.budget, inflight_blocks=used,
                        inflight_request_ids=sorted(self.inflight), acquired_pins=0)
                continue
            protected_count = self.admission_policy.prefix_blocks(len(ids), left)
            if not self.admission_policy.permits(self.pool.get_num_free_blocks(), protected_count, reserve):
                self._capacity_fallback(meta, protected_count, self.pool.get_num_free_blocks(),
                    reserve, left, used)
                continue
            blocks = [self.pool.blocks[i] for i in ids[:protected_count]]
            # Shared references are allowed; acquire our own reference below.
            if any(b.ref_cnt < 0 or b.is_null or b.block_hash is None for b in blocks):
                continue
            if {b.block_id: candidate.block_hashes.get(b.block_id) for b in blocks} != {
                    b.block_id: b.block_hash for b in blocks}:
                self.record("protection_fallback", reason="stale_hash", request_id=rid)
                continue
            protected_meta, continuation = self._front_metadata(candidate, blocks)
            protected_op = protected_meta.op
            proof = build_proof(tuple(op.token_ids[:protected_op.end]), vllm_hash_function=self.hash_fn,
                vllm_initial_hash=kv_cache_utils.NONE_HASH, lmcache_hasher=self.hasher)
            expected = tuple(h for chunk in proof.physical_hashes for h in chunk)
            if tuple(b.block_hash.hex() for b in blocks) != expected:
                self.record("protection_fallback", reason="token_proof", request_id=rid)
                continue
            identity = describe(protected_meta)
            # All validation and the pin happen on the serial scheduler owner.
            refs_before = [b.ref_cnt for b in blocks]
            self.pool.touch(blocks)
            prepared = Prepared(candidate, protected_meta, blocks, identity, proof.chunk_hashes,
                {bid: all_ranks[bid] for bid in ids[:protected_count] if bid in all_ranks}, continuation)
            self.prepared[rid] = prepared
            left -= len(blocks)
            self.record("protection_prepared", **identity, chunk_hashes=list(proof.chunk_hashes),
                acquired_pins=len(blocks), reserve=reserve, free_after=self.pool.get_num_free_blocks(),
                refs_before=refs_before, refs_after=[b.ref_cnt for b in blocks],
                shared_blocks_before=sum(ref > 0 for ref in refs_before))

    def rollback(self, reason):
        self.manager._healthy()
        restore = []
        for rid, prepared in list(self.prepared.items()):
            self.pool.free_blocks(prepared.blocks)
            restore.extend((prepared.free_ranks[b.block_id], b) for b in prepared.blocks
                           if b.ref_cnt == 0 and b.block_id in prepared.free_ranks)
            del self.prepared[rid]
            self.record("protection_rollback", **prepared.identity, reason=reason,
                released_pins=len(prepared.blocks), priority="front_if_unreferenced")
        # Native free_blocks appends hashed blocks to the LRU tail. Doing so on
        # every zero-token lookup poll would protect them indefinitely without
        # a STORE. Put still-unreferenced blocks at the front in original order.
        # We deliberately do NOT reconstruct the whole old queue: allocations,
        # cache hits and frees earlier in this step must retain their new state.
        queue = self.pool.free_block_queue
        restored = [b for _, b in sorted(restore, key=lambda item: item[0])]
        for block in restored:
            queue.remove(block)
        queue.prepend_n(restored)
