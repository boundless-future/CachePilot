"""Lifecycle-only connector for delaying and observing asynchronous KV loads.

This module is intentionally a diagnostic harness.  It does not change the
normal LMCache policy and is never used for performance measurements.  The
worker-side connector delays the retrieve submission on a timer, while the
scheduler-side connector writes request status and block snapshots.  A small
filesystem flag lets the worker timer observe a cancellation that happened in
the scheduler process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector
from vllm.v1.core.sched.scheduler import Scheduler


def _safe_id(request_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", request_id)


class LifecycleConnector(LMCacheMPConnector):
    """Delay worker retrieve submission and record lifecycle observations."""

    def _lifecycle_setup(self) -> None:
        if getattr(self, "_lifecycle_ready", False):
            return
        self._lifecycle_ready = True
        self._lifecycle_dir = Path(
            os.environ.get("CACHEPILOT_LIFECYCLE_DIR", "/tmp/cachepilot-lifecycle")
        )
        self._lifecycle_dir.mkdir(parents=True, exist_ok=True)
        self._cancel_dir = self._lifecycle_dir / "cancelled"
        self._cancel_dir.mkdir(parents=True, exist_ok=True)
        self._delay_seconds = float(os.environ.get("CACHEPILOT_RETRIEVE_DELAY", "8"))
        self._delayed_timers: dict[str, threading.Timer] = {}
        self._submitted_retrieves: set[str] = set()
        self._pending_cancelled_retrieves: set[str] = set()
        self._reported_cancelled_retrieves: set[str] = set()
        self._retrieve_lock = threading.Lock()
        self._tracked_block_ids: set[int] = set()
        self._last_scheduler_state = None
        self._last_worker_state = None
        self._record("process", role=str(getattr(self, "role", "unknown")))

    def _record(self, event: str, **data: Any) -> None:
        self._lifecycle_setup()
        row = dict(
            event=event,
            pid=os.getpid(),
            monotonic_ns=time.monotonic_ns(),
            **data,
        )
        path = self._lifecycle_dir / f"lifecycle-{os.getpid()}.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def _cancel_flag(self, request_id: str) -> Path:
        self._lifecycle_setup()
        return self._cancel_dir / f"{_safe_id(request_id)}.flag"

    @staticmethod
    def _block_ids(scheduler: Scheduler, request_id: str) -> list[int]:
        try:
            manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
            values = manager.req_to_blocks.get(request_id, ())
            return [int(getattr(item, "block_id", item)) for item in values]
        except Exception:
            return []

    def _scheduler_snapshot(self, scheduler: Scheduler, phase: str) -> None:
        requests: list[dict[str, Any]] = []
        queues = (
            ("waiting", getattr(scheduler, "waiting", ())),
            ("skipped_waiting", getattr(scheduler, "skipped_waiting", ())),
            ("running", getattr(scheduler, "running", ())),
        )
        for queue, items in queues:
            for request in items:
                requests.append(
                    dict(
                        request_id=request.request_id,
                        queue=queue,
                        status=getattr(request.status, "name", str(request.status)),
                        num_computed_tokens=request.num_computed_tokens,
                        num_tokens=request.num_tokens,
                        block_ids=self._block_ids(scheduler, request.request_id),
                    )
                )
        pool = scheduler.kv_cache_manager.block_pool
        for item in requests:
            if item["status"] == "WAITING_FOR_REMOTE_KVS":
                self._tracked_block_ids.update(item["block_ids"])
        tracked_refs = {
            block_id: pool.blocks[block_id].ref_cnt
            for block_id in sorted(self._tracked_block_ids)
            if pool.blocks[block_id].ref_cnt
        }
        registered_ids = sorted(scheduler.requests)
        state = (pool.get_num_free_blocks(), tuple(
            (item["request_id"], item["queue"], item["status"],
             item["num_computed_tokens"], tuple(item["block_ids"]))
            for item in requests
        ), tuple(registered_ids), tuple(tracked_refs.items()))
        if state == self._last_scheduler_state:
            return
        self._last_scheduler_state = state
        self._record(
            "scheduler_snapshot",
            phase=phase,
            current_step=getattr(scheduler, "current_step", None),
            free_blocks=pool.get_num_free_blocks(),
            requests=requests,
            registered_ids=registered_ids,
            tracked_refs=tracked_refs,
        )

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        self._lifecycle_setup()
        scheduler_type = Scheduler
        if getattr(scheduler_type.schedule, "_cachepilot_lifecycle", False):
            return
        original = scheduler_type.schedule

        def observed_schedule(scheduler, *args, **kwargs):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, LifecycleConnector):
                connector._scheduler_snapshot(scheduler, "before")
            result = original(scheduler, *args, **kwargs)
            if isinstance(connector, LifecycleConnector):
                connector._scheduler_snapshot(scheduler, "after")
            return result

        observed_schedule._cachepilot_lifecycle = True
        scheduler_type.schedule = observed_schedule

        original_update = scheduler_type._update_from_kv_xfer_finished

        def observed_update(scheduler, output):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, LifecycleConnector) and output.finished_recving:
                connector._record(
                    "scheduler_finished_recving",
                    request_ids=sorted(output.finished_recving),
                )
            result = original_update(scheduler, output)
            if isinstance(connector, LifecycleConnector) and output.finished_recving:
                connector._scheduler_snapshot(scheduler, "after_finished_recving")
            return result

        observed_update._cachepilot_lifecycle = True
        scheduler_type._update_from_kv_xfer_finished = observed_update

    def start_load_kv(self, forward_context, **kwargs: Any) -> None:
        """Schedule retrieve submission without blocking the worker step."""
        self._lifecycle_setup()
        metadata = self._get_connector_metadata()
        request_ids: list[str] = []
        ops = []
        cache_salts: list[str] = []
        request_configs_list: list[dict[str, Any] | None] = []
        for meta in metadata.requests:
            if meta.direction != "RETRIEVE":
                continue
            if meta.request_id in self._submitted_retrieves or meta.request_id in self._delayed_timers:
                continue
            request_ids.append(meta.request_id)
            ops.append(meta.op)
            cache_salts.append(meta.cache_salt)
            request_configs_list.append(meta.request_configs)
        if not request_ids:
            return
        event = self.worker_adapter.create_recorded_event()
        self._record(
            "retrieve_delayed",
            request_ids=request_ids,
            delay_seconds=self._delay_seconds,
            block_ids={rid: list(op.flat_block_ids) for rid, op in zip(request_ids, ops)},
        )

        def submit() -> None:
            with self._retrieve_lock:
                for request_id in request_ids:
                    self._delayed_timers.pop(request_id, None)
                active = [
                    (rid, op, salt, cfg)
                    for rid, op, salt, cfg in zip(
                        request_ids, ops, cache_salts, request_configs_list, strict=True
                    )
                    if not self._cancel_flag(rid).exists()
                ]
                cancelled = set(request_ids) - {item[0] for item in active}
                self._pending_cancelled_retrieves.update(
                    cancelled - self._reported_cancelled_retrieves
                )
                if active:
                    ids, active_ops, salts, configs = zip(*active, strict=True)
                    try:
                        self.worker_adapter.batched_submit_retrieve_requests(
                            list(ids), list(active_ops), event,
                            cache_salts=list(salts), request_configs_list=list(configs),
                        )
                        self._submitted_retrieves.update(ids)
                        self._record("retrieve_submitted", request_ids=list(ids))
                    except Exception as exc:
                        self._record("retrieve_submit_error", request_ids=list(ids), error=repr(exc))
                        raise

        timer = threading.Timer(self._delay_seconds, submit)
        timer.daemon = True
        for request_id in request_ids:
            self._delayed_timers[request_id] = timer
        timer.start()

    def request_finished(self, request, block_ids):
        self._lifecycle_setup()
        request_id = request.request_id
        timer = self._delayed_timers.pop(request_id, None)
        if timer is not None:
            timer.cancel()
        self._cancel_flag(request_id).touch()
        self._record(
            "request_finished",
            request_id=request_id,
            delayed_timer=timer is not None,
            retrieve_submitted=request_id in self._submitted_retrieves,
            block_ids=list(block_ids),
            status=getattr(request.status, "name", str(request.status)),
        )
        return super().request_finished(request, block_ids)

    def get_finished(self, finished_req_ids):
        self._lifecycle_setup()
        before = set(self.worker_adapter.retrieve_futures)
        with self._retrieve_lock:
            delayed_ids = set(self._delayed_timers)
            cancelled = self._pending_cancelled_retrieves | {
                rid for rid in delayed_ids if self._cancel_flag(rid).exists()
            }
            cancelled -= self._reported_cancelled_retrieves
            self._pending_cancelled_retrieves = set()
            self._reported_cancelled_retrieves.update(cancelled)
            # Eager STORE normally reports engine-finished IDs even without a
            # STORE. A delayed load must emit only receiving completion on abort.
            result = super().get_finished(
                finished_req_ids - delayed_ids - self._reported_cancelled_retrieves
            )
        if cancelled:
            self._record("retrieve_cancelled_before_submit", request_ids=sorted(cancelled))
            sending, receiving = result
            result = sending, (receiving or set()) | cancelled
            self._record("worker_cancel_completion", request_ids=sorted(cancelled))
        after = set(self.worker_adapter.retrieve_futures)
        state = (tuple(sorted(before)), tuple(sorted(after)),
                 tuple(sorted(finished_req_ids)),
                 tuple(tuple(sorted(item or ())) for item in result))
        if state != self._last_worker_state:
            self._last_worker_state = state
            self._record(
                "worker_get_finished",
                engine_finished=sorted(finished_req_ids),
                result=[sorted(item) if item is not None else None for item in result],
                retrieve_before=sorted(before),
                retrieve_after=sorted(after),
            )
        return result
