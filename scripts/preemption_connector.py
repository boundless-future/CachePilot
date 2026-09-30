"""Read-only scheduler observations for an explicit preemption experiment."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector
from vllm.v1.core.sched.scheduler import Scheduler


class PreemptionConnector(LMCacheMPConnector):
    def _snapshot(self, scheduler):
        pool = scheduler.kv_cache_manager.block_pool
        tracked = getattr(self, "_preemption_blocks", set())
        refs = {block_id: pool.blocks[block_id].ref_cnt for block_id in sorted(tracked)
                if pool.blocks[block_id].ref_cnt}
        state = (pool.get_num_free_blocks(), tuple(sorted(scheduler.requests)),
                 tuple(refs.items()), len(scheduler.deferred_frees))
        if state != getattr(self, "_preemption_state", None):
            self._preemption_state = state
            self._observe("scheduler_snapshot", free_blocks=state[0],
                          registered_ids=list(state[1]), tracked_refs=refs,
                          deferred_frees=state[3])

    def _observe(self, event, **data):
        path = Path(os.environ.get("CACHEPILOT_PREEMPTION_DIR", "/tmp/cachepilot-preemption"))
        path.mkdir(parents=True, exist_ok=True)
        row = dict(event=event, pid=os.getpid(), monotonic_ns=time.monotonic_ns(), **data)
        with (path / f"preemption-{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        self._observe("block_pool_bound", free_blocks=kv_cache_manager.block_pool.get_num_free_blocks())
        manager = self._lazy_offload_manager
        original_reset = manager.on_request_reset

        def observed_reset(request_id):
            slot = manager._requests._slots.get(request_id)
            batch = slot.in_flight if slot else None
            self._observe("manager_reset_before", request_id=request_id,
                in_flight=batch is not None, pins=len(batch.block_ids) if batch else 0)
            result = original_reset(request_id)
            slot = manager._requests._slots.get(request_id)
            batch = slot.in_flight if slot else None
            self._observe("manager_reset_after", request_id=request_id,
                in_flight=batch is not None, orphaned=batch.orphaned if batch else None)
            return result

        manager.on_request_reset = observed_reset
        if getattr(Scheduler._preempt_request, "_cachepilot_observed", False):
            return

        original_preempt = Scheduler._preempt_request
        original_free = Scheduler._free_blocks
        original_schedule = Scheduler.schedule
        original_update = Scheduler.update_from_output

        def observed_preempt(scheduler, request, *args, **kwargs):
            connector = getattr(scheduler, "connector", None)
            if not isinstance(connector, PreemptionConnector):
                return original_preempt(scheduler, request, *args, **kwargs)
            manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
            blocks = [block.block_id for block in manager.req_to_blocks.get(request.request_id, ())]
            connector._preemption_blocks = set(blocks)
            connector._observe(
                "preempt_before", request_id=request.request_id,
                status=request.status.name, num_preemptions=request.num_preemptions,
                computed_tokens=request.num_computed_tokens, block_ids=blocks,
                free_blocks=scheduler.kv_cache_manager.block_pool.get_num_free_blocks(),
                store_inflight=connector._lazy_offload_manager._requests.has_in_flight(request.request_id),
            )
            result = original_preempt(scheduler, request, *args, **kwargs)
            connector._observe(
                "preempt_after", request_id=request.request_id,
                status=request.status.name, num_preemptions=request.num_preemptions,
                computed_tokens=request.num_computed_tokens,
                free_blocks=scheduler.kv_cache_manager.block_pool.get_num_free_blocks(),
                registered=request.request_id in scheduler.requests,
                store_inflight=connector._lazy_offload_manager._requests.has_in_flight(request.request_id),
            )
            return result

        def observed_schedule(scheduler, *args, **kwargs):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, PreemptionConnector):
                connector._snapshot(scheduler)
            result = original_schedule(scheduler, *args, **kwargs)
            if isinstance(connector, PreemptionConnector):
                connector._snapshot(scheduler)
            return result

        def observed_update(scheduler, *args, **kwargs):
            result = original_update(scheduler, *args, **kwargs)
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, PreemptionConnector):
                connector._snapshot(scheduler)
            return result

        def observed_free(scheduler, request):
            connector = getattr(scheduler, "connector", None)
            result = original_free(scheduler, request)
            if isinstance(connector, PreemptionConnector):
                manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
                connector._observe(
                    "blocks_freed", request_id=request.request_id,
                    registered=request.request_id in scheduler.requests,
                    allocation_present=request.request_id in manager.req_to_blocks,
                    free_blocks=scheduler.kv_cache_manager.block_pool.get_num_free_blocks(),
                )
            return result

        observed_preempt._cachepilot_observed = True
        Scheduler._preempt_request = observed_preempt
        Scheduler._free_blocks = observed_free
        Scheduler.schedule = observed_schedule
        Scheduler.update_from_output = observed_update

    def on_new_request(self, request):
        self._observe("request_arrived", request_id=request.request_id,
                      prompt_tokens=len(request.prompt_token_ids))
        return super().on_new_request(request)

    def wait_for_save(self):
        result = super().wait_for_save()
        futures = self.worker_adapter.store_futures
        identities = {rid: getattr(future, "future", future) for rid, future in futures.items()}
        previous = getattr(self, "_observed_store_futures", {})
        for request_id, future in identities.items():
            if previous.get(request_id) is not future:
                self._observe("worker_store_submitted", request_id=request_id)
        self._observed_store_futures = identities
        return result

    def request_finished(self, request, block_ids):
        self._observe(
            "request_finished", request_id=request.request_id,
            status=request.status.name, num_preemptions=request.num_preemptions,
            block_count=len(block_ids),
        )
        return super().request_finished(request, block_ids)

    def build_connector_worker_meta(self):
        result = super().build_connector_worker_meta()
        if result is not None:
            self._observe(
                "store_receipt", completed=sorted(result.completed_store_requests),
                completed_counts=dict(result.completed_store_requests),
                failed=sorted(result.failed_store_requests),
            )
        return result

    def update_connector_output(self, connector_output):
        meta = connector_output.kv_connector_worker_meta
        if meta is None:
            return super().update_connector_output(connector_output)
        pool = self._kv_cache_manager.block_pool
        registry = self._lazy_offload_manager._requests
        ids = set(meta.completed_store_requests) | set(meta.failed_store_requests)
        pinned = {}
        for request_id in ids:
            slot = registry._slots.get(request_id)
            batch = slot.in_flight if slot else None
            pinned[request_id] = tuple(batch.block_ids) if batch else ()
            self._observe("scheduler_receipt_before", request_id=request_id,
                completed=meta.completed_store_requests.get(request_id, 0),
                failed=request_id in meta.failed_store_requests,
                orphaned=batch.orphaned if batch else None,
                pinned_refs={b: pool.blocks[b].ref_cnt for b in pinned[request_id]})
        result = super().update_connector_output(connector_output)
        for request_id in ids:
            self._observe("scheduler_receipt_after", request_id=request_id,
                in_flight=registry.has_in_flight(request_id),
                pinned_refs={b: pool.blocks[b].ref_cnt for b in pinned[request_id]})
        return result
