"""One-shot diagnostic of a failed worker STORE receipt after real transfer."""

import json
import os
from pathlib import Path
import time

from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector
from vllm.v1.core.sched.scheduler import Scheduler


class FailedStoreResult:
    def __init__(self, original, report):
        self.original = original
        self.report = report

    def query(self):
        return self.original.query()

    def result(self, *args, **kwargs):
        actual = self.original.result(*args, **kwargs)
        self.report(actual)
        return False


class ObservedStoreResult:
    def __init__(self, original, report):
        self.original = original
        self.report = report

    def query(self):
        return self.original.query()

    def result(self, *args, **kwargs):
        actual = self.original.result(*args, **kwargs)
        self.report(actual)
        return actual


class StoreFailureConnector(LMCacheMPConnector):
    def _record(self, event, **fields):
        directory = Path(os.environ["CACHEPILOT_STORE_FAILURE_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        row = dict(event=event, pid=os.getpid(), monotonic_ns=time.monotonic_ns(), **fields)
        with (directory / f"store-failure-{os.getpid()}.jsonl").open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def _snapshot(self, scheduler, phase):
        pool = scheduler.kv_cache_manager.block_pool
        registered = sorted(scheduler.requests)
        state = (pool.get_num_free_blocks(), tuple(registered), len(scheduler.deferred_frees))
        if state != getattr(self, "_last_snapshot", None):
            self._last_snapshot = state
            self._record("scheduler_snapshot", phase=phase, free_blocks=state[0],
                         registered_ids=registered, deferred_frees=state[2])

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        self._record("block_pool_bound", free_blocks=kv_cache_manager.block_pool.get_num_free_blocks())
        if getattr(Scheduler.schedule, "_cachepilot_store_failure", False):
            return
        original = Scheduler.schedule

        def observed_schedule(scheduler, *args, **kwargs):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, StoreFailureConnector):
                connector._snapshot(scheduler, "before")
            result = original(scheduler, *args, **kwargs)
            if isinstance(connector, StoreFailureConnector):
                connector._snapshot(scheduler, "after")
            return result

        observed_schedule._cachepilot_store_failure = True
        Scheduler.schedule = observed_schedule

    def wait_for_save(self):
        result = super().wait_for_save()
        if getattr(self, "_store_injected", False):
            return result
        for request_id, future in list(self.worker_adapter.store_futures.items()):
            self._store_injected = True
            self._record("store_submitted", request_id=request_id)

            def report(actual, request_id=request_id):
                self._record("store_result_overridden", request_id=request_id,
                             actual_result=actual)

            self.worker_adapter.store_futures[request_id] = FailedStoreResult(future, report)
            break
        return result

    def build_connector_worker_meta(self):
        result = super().build_connector_worker_meta()
        if result is not None:
            self._record("worker_store_receipt",
                         completed=dict(result.completed_store_requests),
                         failed=sorted(result.failed_store_requests))
        return result

    def update_connector_output(self, connector_output):
        meta = connector_output.kv_connector_worker_meta
        if meta is None:
            return super().update_connector_output(connector_output)
        manager = self._lazy_offload_manager
        ids = sorted(set(meta.completed_store_requests) | set(meta.failed_store_requests))
        pinned = {}
        pool = self._kv_cache_manager.block_pool
        for request_id in ids:
            slot = manager._requests._slots.get(request_id)
            pinned[request_id] = list(slot.in_flight.block_ids) if slot and slot.in_flight else []
            self._record("scheduler_store_receipt_before", request_id=request_id,
                         completed=meta.completed_store_requests.get(request_id, 0),
                         failed=request_id in meta.failed_store_requests,
                         in_flight=manager._requests.has_in_flight(request_id),
                         pending=manager._policy.has_pending_request(request_id),
                         pinned_refs={bid: pool.blocks[bid].ref_cnt for bid in pinned[request_id]},
                         free_blocks=self._kv_cache_manager.block_pool.get_num_free_blocks())
        result = super().update_connector_output(connector_output)
        for request_id in ids:
            self._record("scheduler_store_receipt_after", request_id=request_id,
                         in_flight=manager._requests.has_in_flight(request_id),
                         pending=manager._policy.has_pending_request(request_id),
                         pinned_refs={bid: pool.blocks[bid].ref_cnt for bid in pinned[request_id]},
                         free_blocks=self._kv_cache_manager.block_pool.get_num_free_blocks())
        return result

    def request_finished(self, request, block_ids):
        self._record("request_finished", request_id=request.request_id,
                     status=request.status.name, block_count=len(block_ids))
        return super().request_finished(request, block_ids)


class ServerRejectedStoreConnector(StoreFailureConnector):
    """Submit one short block list so the MP server rejects the whole STORE."""

    def wait_for_save(self):
        if getattr(self, "_store_injected", False):
            return LMCacheMPConnector.wait_for_save(self)
        context = self.worker_adapter.transfer_ctx
        original = context.submit_store

        def submit_underflow(request_id, key, kv_caches, block_ids, event, blocks_in_chunk):
            self._store_injected = True
            self._record("store_payload_invalidated", request_id=request_id,
                         original_block_counts=[len(group) for group in block_ids],
                         submitted_block_counts=[0 for _ in block_ids])
            future = original(request_id, key, kv_caches, [[] for _ in block_ids],
                              event, blocks_in_chunk)
            self._record("store_submitted", request_id=request_id)
            return future

        context.submit_store = submit_underflow
        try:
            result = LMCacheMPConnector.wait_for_save(self)
        finally:
            context.submit_store = original
        if getattr(self, "_store_injected", False):
            for request_id, future in list(self.worker_adapter.store_futures.items()):
                def report(actual, request_id=request_id):
                    self._record("server_store_result", request_id=request_id,
                                 actual_result=actual)

                self.worker_adapter.store_futures[request_id] = ObservedStoreResult(
                    future, report)
                break
        return result
