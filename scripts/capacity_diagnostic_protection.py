"""Opt-in diagnostic copy of bounded prepare; frozen base stays unchanged."""
import os
from scripts.bounded_protection import BoundedProtection, conservative_reserve, Prepared
from scripts.protection_handoff import describe
from scripts.token_prefix_proof import build_proof


class DiagnosticBoundedProtection(BoundedProtection):
    def _diagnostic(self, stage, **fields):
        if os.environ.get("CACHEPILOT_CAPACITY_DIAGNOSTICS") == "1":
            self.record("protection_candidate_diagnostic", stage=stage,
                diagnostic_step=getattr(self, "_diagnostic_step", 0), **fields)

    def _capacity_fallback(self, meta, required, free, reserve, left, used):
        identity = describe(meta)
        # Diagnostic mode records every owner-thread observation before the
        # existing de-duplicated summary event. It must not inspect or mutate
        # allocator state beyond values already computed by this branch.
        if os.environ.get("CACHEPILOT_CAPACITY_DIAGNOSTICS") == "1":
            self.record("protection_capacity_diagnostic", **identity,
                diagnostic_step=getattr(self, "_diagnostic_step", 0),
                required_blocks=required, available_blocks=left,
                inflight_blocks=used, budget_blocks=self.budget, free=free,
                reserve=reserve, free_minus_reserve_minus_chunk=free - reserve - 16,
                acquired_pins=0)
        observed = getattr(self, "_capacity_observed", set())
        if identity["wire_id"] in observed:
            return
        observed.add(identity["wire_id"])
        self._capacity_observed = observed
        self.record("protection_fallback", reason="capacity", **identity,
            required_blocks=required, available_blocks=left,
            inflight_blocks=used, budget_blocks=self.budget, free=free,
            reserve=reserve, acquired_pins=0)

    def prepare(self, scheduler):
        from vllm.v1.core import kv_cache_utils
        if self.prepared:
            raise RuntimeError("Unsettled PREPARED batch from previous step")
        reserve = conservative_reserve(scheduler)
        if os.environ.get("CACHEPILOT_CAPACITY_DIAGNOSTICS") == "1":
            self._diagnostic_step = getattr(self, "_diagnostic_step", 0) + 1
            self._diagnostic("boundary", reserve=reserve,
                free=self.pool.get_num_free_blocks(), budget=self.budget,
                inflight_blocks=sum(len(p.blocks) for p in self.inflight.values()),
                watermark=scheduler.kv_cache_manager.watermark_blocks,
                max_scheduled_tokens=scheduler.max_num_scheduled_tokens,
                live=[dict(request_id=rid, tokens=r.num_tokens_with_spec,
                    computed=r.num_computed_tokens,
                    unsupported=bool(getattr(r, "mm_features", None) or getattr(r, "lora_request", None)))
                    for rid,r in scheduler.requests.items()],
                pending=[describe(p.store_metadata) for pending in self.policy._pending.values() for p in pending])
            for rid, pending in self.policy._pending.items():
                for index, candidate in enumerate(pending):
                    ids = candidate.store_metadata.op.block_ids
                    self._diagnostic("pending_state", **describe(candidate.store_metadata), index=index,
                        live=rid in scheduler.requests,
                        finished=self.manager._requests.is_finished(rid),
                        native_inflight=self.manager._requests.has_in_flight(rid),
                        protected_inflight=rid in self.inflight,
                        blocks=[dict(id=bid, refs=self.pool.blocks[bid].ref_cnt,
                            null=self.pool.blocks[bid].is_null,
                            hash_matches=self.pool.blocks[bid].block_hash == candidate.block_hashes.get(bid))
                            for group in ids for bid in group])
        if reserve is None:
            self._diagnostic("reserve_unavailable")
            return
        used = sum(len(p.blocks) for p in self.inflight.values())
        left = self.budget - used
        free = self.pool.get_num_free_blocks()
        self._diagnostic("capacity_gate", free=free, reserve=reserve, left=left, used=used)
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
            self._diagnostic("capacity_return", free=free, reserve=reserve, left=left)
            return
        # Use actual full-footprint pressure only to rank imminent candidates.
        all_ranks = self.policy._free_queue_ranks(free)
        ranks = {bid: rank for bid, rank in all_ranks.items() if rank < reserve}
        candidates = []
        for rid, pending in self.policy._pending.items():
            if (rid in scheduler.requests or not self.manager._requests.is_finished(rid)
                    or self.manager._requests.has_in_flight(rid) or rid in self.inflight):
                self._diagnostic("request_ineligible", request_id=rid)
                continue
            op = pending[0]
            positions = [ranks[b] for b in op.block_hashes if b in ranks]
            if positions:
                candidates.append((min(positions), rid, op))
            self._diagnostic("rank", **describe(op.store_metadata), positions=positions)
        for _, rid, candidate in sorted(candidates):
            meta = candidate.store_metadata
            op = meta.op
            if (op.start != 0 or op.end < 256 or op.end % 256 or op.skip_first_n_tokens
                    or meta.cache_salt or len(op.block_ids) != 1):
                self._diagnostic("range_ineligible", **describe(meta))
                continue
            ids = list(op.block_ids[0])
            if len(ids) != (op.end - op.start) // 16 or len(set(ids)) != len(ids):
                self._diagnostic("block_layout_ineligible", **describe(meta))
                continue
            if left < 16:
                self._diagnostic("budget_ineligible", **describe(meta), left=left)
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
            protected_count = min(len(ids), left)
            if protected_count < 16 or protected_count % 16:
                raise RuntimeError("Protection budget must contain complete chunks")
            if self.pool.get_num_free_blocks() - protected_count < reserve:
                self._capacity_fallback(meta, protected_count, self.pool.get_num_free_blocks(),
                    reserve, left, used)
                continue
            blocks = [self.pool.blocks[i] for i in ids[:protected_count]]
            # No live request reference, null block, reused hash or absent hash.
            if any(b.ref_cnt or b.is_null or b.block_hash is None for b in blocks):
                self._diagnostic("reference_ineligible", **describe(meta),
                    blocks=[dict(id=b.block_id, refs=b.ref_cnt, null=b.is_null,
                        hash_present=b.block_hash is not None) for b in blocks])
                continue
            if {b.block_id: candidate.block_hashes.get(b.block_id) for b in blocks} != {
                    b.block_id: b.block_hash for b in blocks}:
                self.record("protection_fallback", reason="stale_hash", request_id=rid)
                self._diagnostic("stale_hash", **describe(meta))
                continue
            protected_meta, continuation = self._front_metadata(candidate, blocks)
            protected_op = protected_meta.op
            proof = build_proof(tuple(op.token_ids[:protected_op.end]), vllm_hash_function=self.hash_fn,
                vllm_initial_hash=kv_cache_utils.NONE_HASH, lmcache_hasher=self.hasher)
            expected = tuple(h for chunk in proof.physical_hashes for h in chunk)
            if tuple(b.block_hash.hex() for b in blocks) != expected:
                self.record("protection_fallback", reason="token_proof", request_id=rid)
                self._diagnostic("token_proof", **describe(meta))
                continue
            identity = describe(protected_meta)
            # All validation and the pin happen on the serial scheduler owner.
            self.pool.touch(blocks)
            prepared = Prepared(candidate, protected_meta, blocks, identity, proof.chunk_hashes,
                {bid: all_ranks[bid] for bid in ids[:protected_count]}, continuation)
            self.prepared[rid] = prepared
            left -= len(blocks)
            self.record("protection_prepared", **identity, chunk_hashes=list(proof.chunk_hashes),
                acquired_pins=len(blocks), reserve=reserve, free_after=self.pool.get_num_free_blocks())
            self._diagnostic("prepared", **identity, blocks=len(blocks))
