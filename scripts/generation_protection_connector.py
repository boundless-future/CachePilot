"""Independent opt-in; real callback contracts precede GPU admission."""
from scripts.generation_ledger_connector import GenerationLedgerConnector
from scripts.generation_protection_bridge import (
    ProtectionGenerationLedger, GenerationProtectionBridge, GenerationBoundedProtection)


class GenerationProtectionConnector(GenerationLedgerConnector):
    ledger_type = ProtectionGenerationLedger
    bridge_type = GenerationProtectionBridge

    def _validate_protection(self, extra):
        if extra.get('cachepilot.generation_protection') is not True:
            raise ValueError('Explicit generation protection opt-in required')

    def _make_bounded_protection(self, config):
        return GenerationBoundedProtection(self, config)

    def _flush_generation_ends(self):
        self._lazy_offload_manager._healthy()
        return super()._flush_generation_ends()

    def generation_snapshot(self):
        result = super().generation_snapshot()
        result['protection_poisoned'] = self._lazy_offload_manager.poisoned
        result['derived_tickets'] = [dict(wire_id=child.wire,
            serial=child.serial, parent_serial=parent.serial)
            for child, parent in self._generation_ledger.derived_from.items()]
        return result

    def build_connector_meta(self, output):
        try:
            return super().build_connector_meta(output)
        except BaseException as exc:
            # Metadata construction can fail after native registration, even
            # before its envelope reaches the worker. Do not resume this core.
            self._lazy_offload_manager.poison(exc)
            raise

    def update_connector_output(self, output):
        # Reject malformed/stale envelopes before entering native mutation.
        self._lazy_offload_manager._healthy()
        meta = output.kv_connector_worker_meta
        if meta is not None:
            from scripts.protection_connector import HandoffWorkerMetadata
            if not isinstance(meta, HandoffWorkerMetadata):
                raise ValueError('Missing original STORE receipt envelope')
            self._handoffs.verify(meta.completed_store_requests,
                meta.failed_store_requests, meta.batch_ids)
        try:
            return super().update_connector_output(output)
        except BaseException as exc:
            self._lazy_offload_manager.poison(exc)
            raise
