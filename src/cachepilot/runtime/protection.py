"""Experimental scheduler-owned pinning before vLLM allocation (TP=1).

First slice: finished, unreferenced requests at prefix zero. Protection may
hand off at most two complete chunks from the front of a longer pending
operation, leaving its unchanged suffix in LMCache's normal pending queue.
Native manager owns pins only after metadata commit, and releases them on its
original receipt. An exception after commit requires engine stop.
"""
from dataclasses import dataclass
from cachepilot.runtime.admission import AdmissionPolicy, ConservativeAdmission, conservative_reserve
from cachepilot.runtime.candidates import ranked_candidates

from cachepilot.runtime.handoff import describe
from cachepilot.runtime.prefix_proof import build_proof


@dataclass
class Prepared:
    pending: object
    metadata: object
    blocks: list
    identity: dict
    chunk_hashes: tuple
    free_ranks: dict
    continuation: object | None




class BoundedProtection:
    admission_policy: AdmissionPolicy = ConservativeAdmission()

    def __init__(self, connector, config):
        import hashlib
        import inspect
        from pathlib import Path
        from vllm.v1.core.sched.scheduler import Scheduler
        from vllm.v1.core.kv_cache_manager import KVCacheManager
        from vllm.utils.hashing import get_hash_fn_by_name
        from lmcache.v1.multiprocess.token_hasher import TokenHasher
        for cls, expected in ((Scheduler, "c1db45f3bbad3a875dd8638331b8870c4380ac1ab4734265ba5c863833a82884"),
                (KVCacheManager, "58800535eda854580d84e1025bf32fe01b2a43ce0bba501b4f3d125e4f2c8afa")):
            if hashlib.sha256(Path(inspect.getsourcefile(cls)).read_bytes()).hexdigest() != expected:
                raise RuntimeError("Unsupported scheduler/allocation implementation")
        self.connector = connector
        self.record = connector._handoff_record
        self.budget = connector._protection_config.max_chunks * 16
        self.hash_fn = get_hash_fn_by_name(config.cache_config.prefix_caching_hash_algo)
        extra = config.kv_transfer_config.kv_connector_extra_config
        # This must match the configured MP server, as with upstream KV events.
        self.hasher = TokenHasher(256, extra.get("hash_algorithm", "blake3"))
        self.prepared = {}
        self.inflight = {}
        self.committed_this_step = set()
        self._inflight_budget_observed = set()
        self._capacity_observed = set()

    def _capacity_fallback(self, meta, required, free, reserve, left, used):
        identity = describe(meta)
        observed = getattr(self, "_capacity_observed", set())
        if identity["wire_id"] in observed:
            return
        observed.add(identity["wire_id"])
        self._capacity_observed = observed
        self.record("protection_fallback", reason="capacity", **identity,
            required_blocks=required, available_blocks=left,
            inflight_blocks=used, budget_blocks=self.budget, free=free,
            reserve=reserve, acquired_pins=0)

    @staticmethod
    def _front_metadata(candidate, blocks):
        """Return a prefix STORE and its still-pending suffix, if any.

        vLLM offers a whole contiguous pending STORE operation. The worker can
        track only one batch per raw ID, so a bounded protected prefix must be
        submitted first and the suffix must remain in policy order until its
        receipt settles. Both metadata objects retain the original token
        ledger and request configuration through ``dataclasses.replace``.
        """
        from dataclasses import replace

        meta = candidate.store_metadata
        op = meta.op
        ids = list(op.block_ids[0])
        if not 0 < len(blocks) <= len(ids):
            raise ValueError("Protected prefix length is outside its pending operation")
        protected_end = op.start + len(blocks) * 16
        prefix = replace(meta, op=replace(op, end=protected_end,
            block_ids=[ids[:len(blocks)]]))
        if protected_end == op.end:
            return prefix, None
        suffix = replace(meta, op=replace(op, start=protected_end,
            block_ids=[ids[len(blocks):]]))
        suffix_hashes = {block_id: candidate.block_hashes[block_id]
                         for block_id in ids[len(blocks):]}
        return prefix, replace(candidate, store_metadata=suffix,
            block_hashes=suffix_hashes)

    def bind(self, kv_manager):
        managers = kv_manager.coordinator.single_type_managers
        if len(managers) != 1 or type(managers[0]).__name__ != "FullAttentionManager":
            raise ValueError("Bounded protection requires one full-attention group")
        if self.connector.scheduler_adapter.lmcache_tokens_per_chunk != 256:
            raise ValueError("Bounded protection requires 256-token LMCache chunks")
        self.pool = kv_manager.block_pool
        self.manager = self.connector._lazy_offload_manager
        self.policy = self.manager._policy
        original = self.manager.on_scheduler_step

        def step(output):
            self.committed_this_step = set()
            stores = self.commit(output.total_num_scheduled_tokens)
            actions = original(output)
            actions.stores_to_submit = stores + actions.stores_to_submit
            return actions

        self.manager.on_scheduler_step = step
        allocate = kv_manager.allocate_slots

        def allocate_slots(*args, **kwargs):
            protected = {b.block_id for p in self.prepared.values() for b in p.blocks}
            result = allocate(*args, **kwargs)
            if protected:
                allocated = [] if result is None else [b.block_id for group in result.blocks for b in group]
                if protected.intersection(allocated):
                    raise RuntimeError("Allocator returned a PREPARED protection block")
                self.record("protection_allocation_boundary", protected_ids=sorted(protected),
                    allocated_ids=allocated, allocation_succeeded=result is not None,
                    free_after=self.pool.get_num_free_blocks())
            return result

        kv_manager.allocate_slots = allocate_slots

    def prepare(self, scheduler):
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
            # No live request reference, null block, reused hash or absent hash.
            if any(b.ref_cnt or b.is_null or b.block_hash is None for b in blocks):
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
            self.pool.touch(blocks)
            prepared = Prepared(candidate, protected_meta, blocks, identity, proof.chunk_hashes,
                {bid: all_ranks[bid] for bid in ids[:protected_count]}, continuation)
            self.prepared[rid] = prepared
            left -= len(blocks)
            self.record("protection_prepared", **identity, chunk_hashes=list(proof.chunk_hashes),
                acquired_pins=len(blocks), reserve=reserve, free_after=self.pool.get_num_free_blocks())

    def rollback(self, reason):
        restore = []
        for rid, prepared in list(self.prepared.items()):
            self.pool.free_blocks(prepared.blocks)
            restore.extend((prepared.free_ranks[b.block_id], b) for b in prepared.blocks
                           if b.ref_cnt == 0)
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

    def commit(self, scheduled_tokens):
        if not scheduled_tokens:
            self.rollback("zero_token_step")
            return []
        stores = []
        for rid, prepared in list(self.prepared.items()):
            pending = self.policy._pending.get(rid, [])
            if (not pending or pending[0] is not prepared.pending
                    or describe(prepared.metadata) != prepared.identity
                    or any(b.block_hash != prepared.pending.block_hashes[b.block_id]
                           or b.ref_cnt < 1 for b in prepared.blocks)):
                raise RuntimeError("PREPARED identity changed before metadata commit")
            if self.manager._requests.has_in_flight(rid):
                raise RuntimeError("STORE appeared during serial preparation")
            # Register BEFORE the native drain: it excludes the whole raw ID.
            # The manager receives our existing references, never touches again.
            self.manager._requests.register_batch(rid, [b.block_id for b in prepared.blocks])
            self.inflight[rid] = prepared
            del self.prepared[rid]
            if prepared.continuation is not None:
                self.policy._pending[rid] = [prepared.continuation, *pending[1:]]
                # Native counters count admitted operations, not transfer
                # batches. The original op remains pending as its suffix;
                # its eventual emission/drop closes that single ledger unit.
                self.record("protection_prefix_split", **prepared.identity,
                    original_end=prepared.pending.store_metadata.op.end,
                    suffix=describe(prepared.continuation.store_metadata))
            elif len(pending) == 1:
                del self.policy._pending[rid]
            else:
                self.policy._pending[rid] = pending[1:]
            if prepared.continuation is None:
                self.policy._counters.emitted += 1
            self.committed_this_step.add(rid)
            stores.append(prepared.metadata)
            self.record("protection_metadata_committed", **prepared.identity,
                owned_pins=len(prepared.blocks), scheduled_tokens=scheduled_tokens)
        return stores

    def receipts(self, batches, failed):
        # Called only after handoff identity validation AND the native manager
        # receipt. Service terminal accounting is independently audited.
        for rid, batch in batches.items():
            if batch["source"] != "protection":
                continue
            original = self.inflight[rid]
            if any(batch[k] != v for k, v in original.identity.items()):
                raise RuntimeError("Receipt changed protected operation identity")
            if self.manager._requests.has_in_flight(rid):
                raise RuntimeError("Original manager receipt did not retire pins")
            del self.inflight[rid]
            self.record("protection_receipt", **original.identity, batch_id=batch["batch_id"],
                failed=rid in failed, released_pins=len(original.blocks),
                free_after=self.pool.get_num_free_blocks(),
                pending_after=len(self.policy._pending.get(rid, [])),
                dropped_failed_store=self.policy._counters.dropped_failed_store)


def install_protection_scheduler(scheduler_type):
    if getattr(scheduler_type.schedule, "_cachepilot_protection", False):
        return
    original = scheduler_type.schedule

    def schedule(scheduler, *args, **kwargs):
        bounded = getattr(scheduler.connector, "_bounded", None)
        if bounded is None:
            return original(scheduler, *args, **kwargs)
        try:
            bounded.prepare(scheduler)
            result = original(scheduler, *args, **kwargs)
            if bounded.prepared:
                raise RuntimeError("Scheduler returned without settling PREPARED metadata")
            return result
        except BaseException:
            # Once committed, never unpin on timeout/cancel/exception: worker
            # may already hold the metadata. Only PREPARED is reversible here.
            bounded.rollback("scheduler_exception")
            raise

    schedule._cachepilot_protection = True
    scheduler_type.schedule = schedule
