"""Independent opt-in; real callback contracts precede GPU admission."""
import json
import os
from pathlib import Path
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

    def shutdown(self):
        try:
            return super().shutdown()
        except BaseException as exc:
            # The inherited END pump can fail before its normal snapshot.
            # Capture ownership without another END, receipt or free attempt.
            if self._handoff_role == 'SCHEDULER':
                try:
                    snapshot = self.generation_snapshot()
                    helper = self._bounded
                    snapshot.update(shutdown_complete=False, shutdown_error=repr(exc),
                        prepared_blocks=sum(len(p.blocks) for p in helper.prepared.values()) if helper else 0,
                        protected_inflight_blocks=sum(len(p.blocks) for p in helper.inflight.values()) if helper else 0)
                    target = os.environ.get('CACHEPILOT_GENERATION_SUMMARY_PATH')
                    if target:
                        path = Path(target)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_name(path.name + '.tmp')
                        temporary.write_text(json.dumps(snapshot, indent=2) + '\n', encoding='utf-8')
                        temporary.replace(path)
                    self._handoff_record('generation_shutdown_failed', **snapshot)
                except BaseException as evidence_error:
                    exc.add_note(f'Failed to preserve shutdown evidence: {evidence_error!r}')
            raise

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
