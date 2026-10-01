"""Ownership integration plus existing pin/whole-chunk KV diagnostics."""
from scripts.diagnostic_connector import DiagnosticConnector
from scripts.owned_mp_connector import OwnedMPConnector
from scripts.owned_service_connector import OwnedServiceConnector
from scripts.preemption_kv_probe_connector import PreemptionKVProbeConnector


class OwnedServicePreemptionConnector(PreemptionKVProbeConnector, OwnedServiceConnector):
    def _snapshot(self, *args):
        if len(args) == 2:
            return OwnedMPConnector._snapshot(self, *args)
        return PreemptionKVProbeConnector._snapshot(self, *args)

    def _record(self, event, **fields):
        if isinstance(event, dict):
            return DiagnosticConnector._record(self, event)
        return OwnedMPConnector._record(self, event, **fields)
