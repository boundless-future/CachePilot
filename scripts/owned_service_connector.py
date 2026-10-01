"""Opt-in TP=1 service ownership connector; stock scheduling and kernels."""
import hashlib
import inspect
from pathlib import Path
import secrets

from scripts.owned_mp_connector import OwnedMPConnector
from scripts.owned_service_protocol import (CLIENT_CONFIG, SEQUENCE_CONFIG,
    OwnedRequestClient, coalesce_owned_store_metadata)


class OwnedServiceConnector(OwnedMPConnector):
    def __init__(self, *args, **kwargs):
        from lmcache.integration.vllm.vllm_multi_process_adapter import LMCacheMPSchedulerAdapter
        source = Path(inspect.getsourcefile(LMCacheMPSchedulerAdapter))
        if hashlib.sha256(source.read_bytes()).hexdigest() != "63fe75512185c17e990c49c172d2a381741cc6cbc0eb6f8c534cc9ff5df2912c":
            raise RuntimeError("Unsupported installed MP adapter")
        self._owned_client = secrets.randbelow(2**62 - 1) + 1
        self._owned_sequence = 0
        from lmcache.integration.vllm import lazy_offload_manager
        manager_source = Path(inspect.getsourcefile(lazy_offload_manager))
        if hashlib.sha256(manager_source.read_bytes()).hexdigest() != "e611da21991cd29757e553acfaeabbdd1332d4743f036f6b35ed0e85c80c5bcd":
            raise RuntimeError("Unsupported installed lazy offload manager")
        original = lazy_offload_manager._coalesce_store_metadata
        if not getattr(original, "_cachepilot_owned", False):
            def coalesce(metadatas):
                return coalesce_owned_store_metadata(metadatas, original)
            coalesce._cachepilot_owned = True
            lazy_offload_manager._coalesce_store_metadata = coalesce
        super().__init__(*args, **kwargs)
        adapter = getattr(self, "scheduler_adapter", None)
        if adapter is not None:
            if adapter.tp_size != 1:
                raise ValueError("Owned service supports TP=1 only")
            adapter.req_clients = {url: OwnedRequestClient(client, self._owned_client)
                                   for url, client in adapter.req_clients.items()}
        adapter = getattr(self, "worker_adapter", None)
        if adapter is not None:
            adapter.req_client = OwnedRequestClient(adapter.req_client)

    def _get_or_create_request_tracker(self, request):
        tracker = super()._get_or_create_request_tracker(request)
        if CLIENT_CONFIG not in tracker.request_configs:
            self._owned_sequence += 1
            tracker.request_configs[CLIENT_CONFIG] = self._owned_client
            tracker.request_configs[SEQUENCE_CONFIG] = self._owned_sequence
        return tracker
