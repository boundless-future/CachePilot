"""Opt-in request-generation bridge for the installed LMCache MP connector."""

from __future__ import annotations

import secrets
import json
import os
from pathlib import Path
import time

from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector
from vllm.v1.core.sched.scheduler import Scheduler


GENERATION_CONFIG = "cachepilot.lookup_generation.v1"


class OwnedMPConnector(LMCacheMPConnector):
    def _get_or_create_request_tracker(self, request):
        tracker = super()._get_or_create_request_tracker(request)
        configs = dict(tracker.request_configs or {})
        nonce = getattr(tracker, "_cachepilot_generation", None)
        if nonce is None:
            nonce = secrets.token_hex(16)
            tracker._cachepilot_generation = nonce
        configs[GENERATION_CONFIG] = nonce
        tracker.request_configs = configs
        return tracker

    def _record(self, event, **fields):
        directory = os.environ.get("CACHEPILOT_OWNED_MP_DIR")
        if not directory:
            return
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        row = dict(event=event, pid=os.getpid(), monotonic_ns=time.monotonic_ns(),
                   **fields)
        with (path / f"owned-connector-{os.getpid()}.jsonl").open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    def _snapshot(self, scheduler, phase):
        manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
        pool = scheduler.kv_cache_manager.block_pool
        requests = []
        for queue in ("waiting", "skipped_waiting", "running"):
            for request in getattr(scheduler, queue, ()):
                requests.append(dict(request_id=request.request_id, queue=queue,
                    status=request.status.name,
                    block_ids=[block.block_id for block in
                        manager.req_to_blocks.get(request.request_id, ())]))
        tracked = getattr(self, "_tracked_blocks", set())
        for request in requests:
            if request["status"] == "WAITING_FOR_REMOTE_KVS":
                tracked.update(request["block_ids"])
        self._tracked_blocks = tracked
        state = (pool.get_num_free_blocks(), tuple(sorted(scheduler.requests)),
                 tuple((item["request_id"], item["status"])
                       for item in requests),
                 tuple((block_id, pool.blocks[block_id].ref_cnt)
                       for block_id in sorted(tracked)
                       if pool.blocks[block_id].ref_cnt),
                 len(scheduler.deferred_frees))
        if state != getattr(self, "_last_snapshot", None):
            self._last_snapshot = state
            self._record("scheduler_snapshot", phase=phase,
                free_blocks=state[0], registered_ids=list(state[1]),
                requests=requests, tracked_refs=dict(state[3]),
                deferred_frees=state[4])

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        if not os.environ.get("CACHEPILOT_OWNED_MP_DIR"):
            return
        if getattr(Scheduler.schedule, "_cachepilot_owned_mp", False):
            return
        original_schedule = Scheduler.schedule
        original_update = Scheduler.update_from_output

        def observed_schedule(scheduler, *args, **kwargs):
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, OwnedMPConnector):
                connector._snapshot(scheduler, "before_schedule")
            result = original_schedule(scheduler, *args, **kwargs)
            if isinstance(connector, OwnedMPConnector):
                connector._snapshot(scheduler, "after_schedule")
            return result

        def observed_update(scheduler, *args, **kwargs):
            result = original_update(scheduler, *args, **kwargs)
            connector = getattr(scheduler, "connector", None)
            if isinstance(connector, OwnedMPConnector):
                connector._snapshot(scheduler, "after_update")
            return result

        observed_schedule._cachepilot_owned_mp = True
        Scheduler.schedule = observed_schedule
        Scheduler.update_from_output = observed_update


from scripts.retrieve_failure_connector import ServerRejectedRetrieveConnector


class OwnedRejectedRetrieveConnector(ServerRejectedRetrieveConnector,
                                     OwnedMPConnector):
    """Existing one-shot underflow probe with the generation bridge enabled."""
