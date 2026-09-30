"""Opt-in ordering experiment: preserve futures until the pinned END drains them.

This restores the adapter's documented synchronous ordering. It may block the
scheduler until MQ timeout, and does not solve timeout/death reclamation or
already-consumed prefetch locks. Never enable as a performance strategy.
"""
import hashlib
import inspect
from pathlib import Path


def install_ordering(adapter, record=lambda *args, **kwargs: None):
    original_cleanup = adapter.cleanup_lookup_result

    def cleanup(request_id):
        ack = adapter._unacked_lookups.get(request_id)
        status = adapter._lookup_status.get(request_id)
        original_cleanup(request_id)
        if ack is not None:
            adapter._unacked_lookups[request_id] = ack
        if status is not None:
            adapter._lookup_status[request_id] = status
        record("cleanup_futures_preserved", request_id,
               ack_urls=sorted(ack.futures) if ack is not None else [],
               status_urls=sorted(status) if status else [])

    adapter.cleanup_lookup_result = cleanup


def check_source():
    from lmcache.integration.vllm.vllm_multi_process_adapter import LMCacheMPSchedulerAdapter
    path = Path(inspect.getsourcefile(LMCacheMPSchedulerAdapter))
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = "63fe75512185c17e990c49c172d2a381741cc6cbc0eb6f8c534cc9ff5df2912c"
    if actual != expected:
        raise RuntimeError(f"Unsupported scheduler adapter source: {actual}")


def __getattr__(name):
    # Keep the pure installation helper importable on the CPU development host.
    if name != "LookupOrderingConnector":
        raise AttributeError(name)
    from scripts.lookup_timeline_connector import LookupTimelineConnector

    class LookupOrderingConnector(LookupTimelineConnector):
        def __init__(self, vllm_config, role, kv_cache_config=None):
            check_source()
            super().__init__(vllm_config, role, kv_cache_config)
            adapter = getattr(self, "scheduler_adapter", None)
            if adapter is not None:
                install_ordering(adapter, self._record)

    return LookupOrderingConnector
