"""One-shot worker-boundary failure probe for a completed async retrieve."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector
from vllm.v1.core.sched.scheduler import Scheduler


class FailedResultAfterRetrieve:
    """Keep the real transfer and completion timing, then report one failure."""

    def __init__(self, original, report):
        self.original = original
        self.report = report

    def query(self):
        return self.original.query()

    def result(self, *args, **kwargs):
        actual = self.original.result(*args, **kwargs)
        self.report(actual)
        return False


class RetrieveFailureConnector(LMCacheMPConnector):
    def _record(self, event, **fields):
        directory = Path(os.environ["CACHEPILOT_RETRIEVE_FAILURE_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        row = dict(event=event, pid=os.getpid(), monotonic_ns=time.monotonic_ns(), **fields)
        with (directory / f"retrieve-failure-{os.getpid()}.jsonl").open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def _snapshot(self, scheduler, phase):
        manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
        pool = scheduler.kv_cache_manager.block_pool
        requests = []
        for queue in ("waiting", "skipped_waiting", "running"):
            for request in getattr(scheduler, queue, ()):
                requests.append(dict(request_id=request.request_id, queue=queue,
                                     status=request.status.name,
                                     computed_tokens=request.num_computed_tokens,
                                     block_ids=[block.block_id for block in
                                                manager.req_to_blocks.get(request.request_id, ())]))
        tracked = getattr(self, "_tracked_blocks", set())
        for request in requests:
            if request["status"] == "WAITING_FOR_REMOTE_KVS":
                tracked.update(request["block_ids"])
        self._tracked_blocks = tracked
        state = (pool.get_num_free_blocks(), tuple(sorted(scheduler.requests)),
                 tuple((item["request_id"], item["status"], item["computed_tokens"])
                       for item in requests),
                 tuple((block_id, pool.blocks[block_id].ref_cnt) for block_id in sorted(tracked)
                       if pool.blocks[block_id].ref_cnt), len(scheduler.deferred_frees))
        if state != getattr(self, "_last_snapshot", None):
            self._last_snapshot = state
            self._record("scheduler_snapshot", phase=phase, free_blocks=state[0],
                         registered_ids=list(state[1]), requests=requests,
                         tracked_refs=dict(state[3]), deferred_frees=state[4])

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        if getattr(Scheduler.schedule, "_cachepilot_retrieve_failure", False):
            return
        original_schedule = Scheduler.schedule
        original_update = Scheduler.update_from_output
        original_invalid = Scheduler._handle_invalid_blocks
        original_finished = Scheduler._update_from_kv_xfer_finished

        def observed_schedule(scheduler, *args, **kwargs):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, RetrieveFailureConnector):
                connector._snapshot(scheduler, "before_schedule")
            result = original_schedule(scheduler, *args, **kwargs)
            if isinstance(connector, RetrieveFailureConnector):
                connector._snapshot(scheduler, "after_schedule")
            return result

        def observed_update(scheduler, *args, **kwargs):
            result = original_update(scheduler, *args, **kwargs)
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, RetrieveFailureConnector):
                connector._snapshot(scheduler, "after_update")
            return result

        def observed_invalid(scheduler, invalid_block_ids, num_scheduled_tokens):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, RetrieveFailureConnector):
                connector._record("scheduler_invalid_blocks", block_ids=sorted(invalid_block_ids),
                                  recompute=scheduler.recompute_kv_load_failures)
            result = original_invalid(scheduler, invalid_block_ids, num_scheduled_tokens)
            if isinstance(connector, RetrieveFailureConnector):
                connector._record("scheduler_invalid_handled", skipped_ids=sorted(result),
                                  failed_recving=sorted(scheduler.failed_recving_kv_req_ids))
                connector._snapshot(scheduler, "after_invalid")
            return result

        def observed_finished(scheduler, output):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, RetrieveFailureConnector) and output.finished_recving:
                connector._record("scheduler_finished_recving", request_ids=sorted(output.finished_recving))
            return original_finished(scheduler, output)

        observed_schedule._cachepilot_retrieve_failure = True
        Scheduler.schedule = observed_schedule
        Scheduler.update_from_output = observed_update
        Scheduler._handle_invalid_blocks = observed_invalid
        Scheduler._update_from_kv_xfer_finished = observed_finished

    def start_load_kv(self, forward_context, **kwargs):
        super().start_load_kv(forward_context, **kwargs)
        if getattr(self, "_injected", False):
            return
        for request_id, (future, block_ids) in list(self.worker_adapter.retrieve_futures.items()):
            self._injected = True
            self._record("retrieve_submitted", request_id=request_id,
                         block_ids=list(block_ids))

            def report(actual, request_id=request_id):
                self._record("retrieve_result_overridden", request_id=request_id,
                             actual_result=actual)

            self.worker_adapter.retrieve_futures[request_id] = (
                FailedResultAfterRetrieve(future, report), block_ids)
            break

    def get_finished(self, finished_req_ids):
        before = set(self.worker_adapter.retrieve_futures)
        result = super().get_finished(finished_req_ids)
        after = set(self.worker_adapter.retrieve_futures)
        if before or (result[1] or set()):
            self._record("worker_get_finished", before=sorted(before), after=sorted(after),
                         receiving=sorted(result[1] or ()))
        return result

    def get_block_ids_with_load_errors(self):
        errors = super().get_block_ids_with_load_errors()
        if errors:
            self._record("worker_load_errors", block_ids=sorted(errors))
        return errors

    def request_finished(self, request, block_ids):
        self._record("request_finished", request_id=request.request_id,
                     status=request.status.name, block_count=len(block_ids))
        return super().request_finished(request, block_ids)
