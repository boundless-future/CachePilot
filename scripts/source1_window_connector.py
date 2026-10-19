"""Passive per-step observation of event 1's pending STORE lifecycle."""
from scripts.whole_step_identity_connector import WholeStepIdentityConnector
from cachepilot.runtime.whole_step import whole_step_envelope
from cachepilot.runtime.handoff import describe


SOURCE_REQUEST_ID = "cmpl-cachepilot-trace-0001-0"


def _hex(value):
    return None if value is None else value.hex()


class Source1WindowConnector(WholeStepIdentityConnector):
    """Record candidate state without touching allocation or policy decisions."""

    def bind_kv_cache_manager(self, manager):
        super().bind_kv_cache_manager(manager)
        if self._handoff_role != "SCHEDULER":
            return
        self._last_scheduler = None
        self._window_observation_serial = 0
        policy = self._lazy_offload_manager._policy
        original_drain = policy.drain

        def observed_drain(signals):
            self._record_source_window("policy_drain_before")
            result = original_drain(signals)
            self._record_source_window("policy_drain_after",
                emitted=[dict(request_id=item.request_id,
                    ranges=[[meta.op.start, meta.op.end] for meta, _ in item.metadatas])
                    for item in result.items],
                emptied=list(result.emptied_request_ids))
            return result

        policy.drain = observed_drain
        original_add = policy.add

        def observed_add(metadata, block_hashes):
            result = original_add(metadata, block_hashes)
            if metadata.request_id == SOURCE_REQUEST_ID:
                self._handoff_record("source1_candidate_offered", **describe(metadata))
                self._record_source_window("policy_add_after")
            return result

        policy.add = observed_add
        for name in ("drop_request", "mark_store_failed", "discard_for_reuse"):
            original = getattr(policy, name)

            def observed_drop(request_id, original=original, name=name):
                if request_id == SOURCE_REQUEST_ID:
                    self._record_source_window(name + "_before")
                    self._handoff_record("source1_pending_removed", reason=name,
                                        request_id=request_id)
                return original(request_id)

            setattr(policy, name, observed_drop)
        from vllm.v1.core.sched.scheduler import Scheduler
        original_schedule = Scheduler.schedule
        if not getattr(original_schedule, "_cachepilot_source1_window", False):
            def schedule(scheduler, *args, **kwargs):
                connector = getattr(scheduler, "connector", None)
                observe = getattr(connector, "_record_source_window", None)
                if observe is not None:
                    connector._last_scheduler = scheduler
                    observe("schedule_before")
                result = original_schedule(scheduler, *args, **kwargs)
                if observe is not None:
                    observe("schedule_after")
                return result

            schedule._cachepilot_source1_window = True
            Scheduler.schedule = schedule

    def build_connector_meta(self, scheduler_output):
        result = super().build_connector_meta(scheduler_output)
        self._record_source_window("connector_metadata_built")
        return result

    def _record_source_window(self, stage, **extra):
        if self._handoff_role != "SCHEDULER":
            return
        manager = self._lazy_offload_manager
        policy = manager._policy
        operations = policy._pending.get(SOURCE_REQUEST_ID)
        if not operations:
            return
        scheduler = self._last_scheduler
        if scheduler is None:
            return
        self._window_observation_serial += 1
        free = self._bounded.pool.get_num_free_blocks() if self._bounded else \
            manager._gpu_block_pool.get_num_free_blocks()
        reserve_envelope = whole_step_envelope(scheduler)
        reserve = None if reserve_envelope is None else reserve_envelope.reserve
        ranks = policy._free_queue_ranks(free)
        rows = []
        all_hashes_intact = True
        any_threatened = False
        for operation in operations:
            meta = operation.store_metadata
            op = meta.op
            blocks = []
            for group in op.block_ids:
                for block_id in group:
                    block = manager._gpu_block_pool.blocks[block_id]
                    expected = operation.block_hashes.get(block_id)
                    actual = block.block_hash
                    intact = expected is not None and actual == expected
                    all_hashes_intact &= intact
                    rank = ranks.get(block_id)
                    threatened = reserve is not None and rank is not None and rank < reserve
                    any_threatened |= threatened
                    blocks.append(dict(block_id=block_id,
                        expected_hash=_hex(expected), current_hash=_hex(actual),
                        hash_intact=intact, ref_count=block.ref_cnt,
                        is_null=block.is_null, free_rank=rank,
                        inside_reserve=threatened))
            rows.append(dict(start=op.start, end=op.end,
                skip_first_n_tokens=getattr(op, "skip_first_n_tokens", 0),
                token_count=len(op.token_ids), blocks=blocks))
        lifecycle = manager._requests
        active = SOURCE_REQUEST_ID in scheduler.requests
        finished = lifecycle.is_finished(SOURCE_REQUEST_ID)
        inflight = lifecycle.has_in_flight(SOURCE_REQUEST_ID)
        reserve_fits = reserve is not None and free >= reserve + 16
        safe_blocks = 0 if not reserve_fits else min(
            32, ((free - reserve) // 16) * 16)
        self._handoff_record("source1_window_snapshot",
            observation=self._window_observation_serial, stage=stage,
            request_id=SOURCE_REQUEST_ID, active=active, finished=finished,
            store_inflight=inflight, pending=[dict(op=row) for row in rows],
            free_blocks=free, reserve_blocks=reserve,
            reserve_supported=reserve is not None, reserve_fits=reserve_fits,
            max_safe_protection_blocks=safe_blocks,
            all_pending_hashes_intact=all_hashes_intact,
            any_pending_block_inside_reserve=any_threatened,
            actionable_current_front=(finished and not active and not inflight
                and all_hashes_intact and any_threatened and reserve_fits),
            **extra)
