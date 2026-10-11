"""Read-only natural preemption/allocation observations while a STORE is pinned.

The inherited diagnostic gate delays receipt delivery, not DMA execution.
No scheduler decision, allocation result or request status is changed here.
"""
from scripts.protection_shared_cancel_probe import SharedCancelProbeConnector


class ProtectedPreemptionConnector(SharedCancelProbeConnector):
    def _held_state(self):
        target = getattr(self, "_shared_target", None)
        if not target or target["request_id"] not in self._bounded.inflight:
            return None
        blocks = self._bounded.inflight[target["request_id"]].blocks
        return dict(batch_id=target["batch_id"], original_request_id=target["request_id"],
            refs={b.block_id: b.ref_cnt for b in blocks},
            hashes={b.block_id: b.block_hash.hex() if b.block_hash else None for b in blocks},
            manager_inflight=self._lazy_offload_manager._requests.has_in_flight(target["request_id"]))

    def _snapshot(self, *args):
        result = super()._snapshot(*args)
        if len(args) == 2 and (state := self._held_state()) is not None:
            signature = repr(state)
            if signature != getattr(self, "_last_held_state", None):
                self._last_held_state = signature
                self._handoff_record("protected_pressure_state", **state)
        return result

    def on_new_request(self, request):
        state = self._held_state()
        if state is not None:
            self._handoff_record("protected_pressure_arrived", request_id=request.request_id,
                prompt_tokens=len(request.prompt_token_ids), first_token=request.prompt_token_ids[0], **state)
        return super().on_new_request(request)

    def request_finished(self, request, block_ids):
        state = self._held_state()
        if state is not None:
            self._handoff_record("protected_pressure_finished", request_id=request.request_id,
                status=request.status.name, num_preemptions=request.num_preemptions, **state)
        return super().request_finished(request, block_ids)

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        pool = kv_cache_manager.block_pool
        allocate = pool.get_new_blocks

        def observed_allocate(*args, **kwargs):
            state = self._held_state()
            result = allocate(*args, **kwargs)
            if state is not None:
                self._handoff_record("protected_pressure_allocation", **state,
                    allocated_ids=[b.block_id for b in result], after=self._held_state())
            return result

        pool.get_new_blocks = observed_allocate
        from vllm.v1.core.sched.scheduler import Scheduler
        if getattr(Scheduler._preempt_request, "_cachepilot_protected_pressure", False):
            return
        original = Scheduler._preempt_request

        def preempt(scheduler, request, *args, **kwargs):
            connector = getattr(scheduler, "connector", None)
            state = connector._held_state() if isinstance(connector, ProtectedPreemptionConnector) else None
            if state is not None:
                manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
                ids = [b.block_id for b in manager.req_to_blocks.get(request.request_id, ())]
                connector._handoff_record("protected_preempt_before", **state,
                    request_id=request.request_id, status=request.status.name,
                    num_preemptions=request.num_preemptions, computed_tokens=request.num_computed_tokens,
                    victim_blocks=ids, free_blocks=pool.get_num_free_blocks())
            result = original(scheduler, request, *args, **kwargs)
            if state is not None:
                connector._handoff_record("protected_preempt_after", **connector._held_state(),
                    request_id=request.request_id, status=request.status.name,
                    num_preemptions=request.num_preemptions, computed_tokens=request.num_computed_tokens,
                    free_blocks=pool.get_num_free_blocks())
            return result

        preempt._cachepilot_protected_pressure = True
        Scheduler._preempt_request = preempt
