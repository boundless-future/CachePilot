"""Observe scheduler-side lookup cancellation without changing LMCache decisions."""

import json
import os
from pathlib import Path
import time

from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector


class LookupTimelineConnector(LMCacheMPConnector):
    def __init__(self, vllm_config, role, kv_cache_config=None):
        super().__init__(vllm_config, role, kv_cache_config)
        adapter = getattr(self, "scheduler_adapter", None)
        if adapter is None:
            return
        directory = Path(os.environ["CACHEPILOT_LOOKUP_TIMELINE_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        self._timeline_path = directory / f"lookup-{os.getpid()}.jsonl"

        for url, client in adapter.req_clients.items():
            for method_name in ("lookup", "query_prefetch_status", "end_session"):
                original = getattr(client, method_name)

                def observed(*args, _original=original, _name=method_name,
                             _url=url, **kwargs):
                    request_id = (args[0].request_id if _name == "lookup"
                                  else args[0])
                    self._record("rpc_submit", request_id, method=_name, url=_url)
                    return _original(*args, **kwargs)

                setattr(client, method_name, observed)

        for method_name in ("maybe_submit_lookup_request", "check_lookup_result",
                            "cleanup_lookup_result", "end_session"):
            original = getattr(adapter, method_name)

            def observed(request_id, *args, _original=original, _name=method_name,
                         **kwargs):
                before = self._lookup_state(request_id)
                self._record("adapter_before", request_id, method=_name, state=before)
                try:
                    result = _original(request_id, *args, **kwargs)
                except BaseException as exc:
                    self._record("adapter_error", request_id, method=_name,
                                 error=repr(exc), state=self._lookup_state(request_id))
                    raise
                self._record("adapter_after", request_id, method=_name,
                             state=self._lookup_state(request_id), result=result)
                return result

            setattr(adapter, method_name, observed)

    def _lookup_state(self, request_id):
        adapter = self.scheduler_adapter
        ack = adapter._unacked_lookups.get(request_id)
        return dict(
            pending=request_id in adapter._pending_lookups,
            unacked_urls=sorted(ack.futures) if ack else [],
            status_urls=sorted(adapter._lookup_status.get(request_id, {})),
            finished=request_id in adapter._finished_lookup_results,
        )

    def _record(self, event, request_id, **fields):
        row = dict(event=event, request_id=request_id, pid=os.getpid(),
                   monotonic_ns=time.monotonic_ns(), unix_time=time.time(), **fields)
        with self._timeline_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
