"""Target a protected STORE with a server host exception after actual enqueue.

The original future value is observed unchanged. This is controlled host-side
failure, not a driver fault, a DMA interruption, or a natural hardware failure.
"""
import os
from pathlib import Path

from scripts.protection_probe_connector import ProtectionProbeConnector
from scripts.store_failure_connector import ObservedStoreResult


class StoreFaultProbeConnector(ProtectionProbeConnector):
    def wait_for_save(self):
        batch = None
        if not getattr(self, "_store_fault_selected", False):
            batch = next((b for b in self._bound_handoffs if b["source"] == "protection"), None)
        if batch:
            self._store_fault_selected = True
            # Both processes share this diagnostic directory. Publish the
            # complete wire identity before any STORE RPC for this batch.
            Path(os.environ["CACHEPILOT_OWNED_FAULT_TARGET"]).write_text(batch["wire_id"])
            (Path(os.environ["CACHEPILOT_OWNED_SERVICE_DIR"]) / "armed-transfer").touch()
            self._handoff_record("protected_server_fault_armed", **batch)
        result = super().wait_for_save()
        if batch:
            rid = batch["request_id"]
            future = self.worker_adapter.store_futures[rid]
            def report(actual):
                self._handoff_record("protected_server_fault_result", **batch,
                    actual_result=actual, delivered_result=actual)
            self.worker_adapter.store_futures[rid] = ObservedStoreResult(future, report)
        return result
