"""Shared synchronizing KV probe for zero-budget correctness comparisons."""
from scripts.diagnostic_connector import DiagnosticConnector
from scripts.owned_mp_connector import OwnedMPConnector
from scripts.preemption_probe_range import whole_loaded_chunks
from scripts.protection_connector import HandoffObservedConnector, ProtectionConnector


class ProbeMixin(DiagnosticConnector):
    def _record(self, event, **fields):
        if isinstance(event, dict):
            return DiagnosticConnector._record(self, event)
        return OwnedMPConnector._record(self, event, **fields)

    def _snapshot(self, *args):
        if len(args) == 2:
            return OwnedMPConnector._snapshot(self, *args)
        phase, rid, op, salt = args
        if phase == "after_retrieve":
            op = whole_loaded_chunks(op, block_tokens=16,
                chunk_tokens=self.worker_adapter.lmcache_tokens_per_chunk)
            if op is None:
                return
        return DiagnosticConnector._snapshot(self, phase, rid, op, salt)


class ObservedEvictionProbeConnector(ProbeMixin, HandoffObservedConnector):
    pass


class ProtectionProbeConnector(ProbeMixin, ProtectionConnector):
    pass
