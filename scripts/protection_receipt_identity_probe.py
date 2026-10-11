"""Diagnostic invalid-envelope injection at the live scheduler handoff boundary."""
from types import SimpleNamespace

from scripts.protection_connector import HandoffWorkerMetadata
from scripts.protection_probe_connector import ProtectionProbeConnector
from scripts.protection_shared_cancel_probe import SharedCancelProbeConnector


class ReceiptIdentityProbeConnector(SharedCancelProbeConnector):
    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        self._native_receipt_calls = 0
        original = self._lazy_offload_manager.on_store_results

        def observed_results(*args, **kwargs):
            self._native_receipt_calls += 1
            return original(*args, **kwargs)

        self._lazy_offload_manager.on_store_results = observed_results

    def _receipt_state(self):
        target = self._shared_target
        blocks = [self._bounded.pool.blocks[i] for i in target["block_ids"][0]]
        rid = target["request_id"]
        return dict(refs={b.block_id: b.ref_cnt for b in blocks},
            hashes={b.block_id: b.block_hash.hex() if b.block_hash else None for b in blocks},
            protection_inflight=rid in self._bounded.inflight,
            manager_inflight=self._lazy_offload_manager._requests.has_in_flight(rid),
            ledger_batch_id=self._handoffs.pending.get(rid, {}).get("batch_id"),
            native_receipt_calls=self._native_receipt_calls,
            free_blocks=self._bounded.pool.get_num_free_blocks())

    def _reject(self, label, envelope, injected_id):
        target = self._shared_target
        before = self._receipt_state()
        try:
            # Calls the actual scheduler handoff verifier; a rejected envelope
            # must never reach native manager.on_store_results.
            ProtectionProbeConnector.update_connector_output(self,
                SimpleNamespace(kv_connector_worker_meta=envelope))
        except ValueError as exc:
            after = self._receipt_state()
            if before != after:
                raise RuntimeError("Rejected receipt mutated live ownership") from exc
            self._handoff_record("receipt_identity_rejected", case=label,
                batch_id=target["batch_id"], request_id=target["request_id"],
                injected_batch_id=injected_id, error=str(exc), before=before, after=after)
            return
        raise RuntimeError("Invalid STORE receipt was accepted")

    def build_connector_meta(self, scheduler_output):
        result = super().build_connector_meta(scheduler_output)
        target = getattr(self, "_shared_target", None)
        if target and not getattr(self, "_stale_injected", False):
            rid = target["request_id"]
            # This is a deliberately stale-shaped batch identity for the same
            # raw ID. It does not claim the API reused a real request ID.
            stale = target["batch_id"] + ":prior-generation"
            envelope = HandoffWorkerMetadata({rid: 1}, set(), {rid: stale})
            self._reject("synthetic_stale_batch", envelope, stale)
            self._stale_injected = True
        return result

    def update_connector_output(self, connector_output):
        meta = connector_output.kv_connector_worker_meta
        target = getattr(self, "_shared_target", None)
        duplicate = (target is not None and meta is not None
            and target["request_id"] in meta.completed_store_requests
            and not getattr(self, "_duplicate_injected", False))
        result = super().update_connector_output(connector_output)
        if duplicate:
            rid = target["request_id"]
            envelope = HandoffWorkerMetadata({rid: 1}, set(), {rid: target["batch_id"]})
            self._reject("duplicate_after_terminal", envelope, target["batch_id"])
            self._duplicate_injected = True
        return result
