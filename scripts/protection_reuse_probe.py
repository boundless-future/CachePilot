"""Real API ID reuse; replay captured old envelopes against the new STORE.

Only the diagnostic instance disables vLLM's request ID randomization. The
old STORE executes normally; its successful receipt can optionally be reported
as failed to exercise orphan handling. This is not a device execution fault.
"""
import os
from types import SimpleNamespace

from scripts.protection_connector import HandoffWorkerMetadata
from scripts.protection_handoff import describe
from scripts.protection_probe_connector import ProtectionProbeConnector
from scripts.protection_shared_cancel_probe import GatedResult
from scripts.owned_service_protocol import wire_id


class ReuseResult(GatedResult):
    def result(self, *args, **kwargs):
        actual = super().result(*args, **kwargs)
        delivered = os.environ.get("CACHEPILOT_REUSE_OLD_FAILURE") != "1"
        self.record("reuse_result", actual_result=actual, delivered_result=delivered)
        return delivered


class ReuseProbeConnector(ProtectionProbeConnector):
    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        self._native_receipt_calls = 0
        original = self._lazy_offload_manager.on_store_results

        def observed(*args, **kwargs):
            self._native_receipt_calls += 1
            return original(*args, **kwargs)

        self._lazy_offload_manager.on_store_results = observed

    def _reuse_state(self):
        rid = self._reuse_target["request_id"]
        manager = self._lazy_offload_manager
        batch = self._handoffs.pending.get(rid)
        ids = set(self._reuse_target["block_ids"][0])
        if batch:
            ids.update(batch["block_ids"][0])
        return dict(native_receipt_calls=self._native_receipt_calls,
            ledger=dict(batch) if batch else None,
            pending=[describe(p.store_metadata) for p in manager._policy._pending.get(rid, [])],
            broken=rid in manager._policy._broken_prefixes,
            manager_inflight=manager._requests.has_in_flight(rid),
            orphaned=manager._requests.in_flight_is_orphaned(rid),
            protection_inflight=rid in self._bounded.inflight,
            refs={i: self._bounded.pool.blocks[i].ref_cnt for i in sorted(ids)},
            hashes={i: self._bounded.pool.blocks[i].block_hash.hex()
                    if self._bounded.pool.blocks[i].block_hash else None for i in sorted(ids)},
            free=self._bounded.pool.get_num_free_blocks())

    def _get_or_create_request_tracker(self, request):
        tracker = super()._get_or_create_request_tracker(request)
        target = getattr(self, "_reuse_target", None)
        if target and request.request_id == target["request_id"]:
            identity = wire_id(SimpleNamespace(request_id=request.request_id,
                request_configs=tracker.request_configs))
            if identity != target["wire_id"] and not getattr(self, "_reuse_arrived", False):
                self._reuse_arrived = True
                self._handoff_record("reuse_new_generation", request_id=request.request_id,
                    old_batch_id=target["batch_id"], old_wire_id=target["wire_id"],
                    new_wire_id=identity, state=self._reuse_state())
        return tracker

    def request_finished(self, request, block_ids):
        result = super().request_finished(request, block_ids)
        target = getattr(self, "_reuse_target", None)
        if target and request.request_id == target["request_id"]:
            self._handoff_record("reuse_new_finished", request_id=request.request_id,
                status=request.status.name, state=self._reuse_state())
        return result

    def build_connector_meta(self, scheduler_output):
        metadata = super().build_connector_meta(scheduler_output)
        for batch in metadata.cachepilot_handoffs_v1:
            target = getattr(self, "_reuse_target", None)
            if target is None and batch["source"] == "protection":
                self._reuse_target = batch
                self._handoff_record("reuse_target", **batch)
            elif target and batch["request_id"] == target["request_id"]:
                if batch["wire_id"] == target["wire_id"]:
                    raise RuntimeError("Reuse diagnostic expects a different generation")
                self._handoff_record("reuse_new_batch", **batch)
                for label, failed in (("old_success", False), ("old_failure", True),
                                      ("duplicate_old_success", False)):
                    self._reject_old(label, failed, batch["batch_id"])
        return metadata

    def _reject_old(self, label, failed, new_batch_id=None):
        target = self._reuse_target
        rid = target["request_id"]
        before = self._reuse_state()
        envelope = HandoffWorkerMetadata({rid: 1}, {rid} if failed else set(),
            {rid: target["batch_id"]})
        try:
            ProtectionProbeConnector.update_connector_output(self,
                SimpleNamespace(kv_connector_worker_meta=envelope))
        except ValueError as exc:
            after = self._reuse_state()
            if before != after:
                raise RuntimeError("Old envelope mutated successor") from exc
            self._handoff_record("reuse_old_rejected", case=label,
                old_batch_id=target["batch_id"], new_batch_id=new_batch_id,
                request_id=rid, error=str(exc), before=before, after=after)
        else:
            raise RuntimeError("Old envelope was accepted for successor")

    def wait_for_save(self):
        batches = list(self._bound_handoffs)
        result = super().wait_for_save()
        if getattr(self, "_reuse_gate_installed", False):
            return result
        for batch in batches:
            if batch["source"] != "protection":
                continue
            rid = batch["request_id"]
            def record(event, **fields):
                self._handoff_record(event, batch_id=batch["batch_id"], request_id=rid, **fields)
            self.worker_adapter.store_futures[rid] = ReuseResult(
                self.worker_adapter.store_futures[rid], os.environ["CACHEPILOT_SHARED_CANCEL_GATE"], record)
            self._reuse_gate_installed = True
            record("shared_gate_installed")
            break
        return result

    def update_connector_output(self, connector_output):
        meta = connector_output.kv_connector_worker_meta
        target = getattr(self, "_reuse_target", None)
        relevant = target and meta and target["request_id"] in meta.completed_store_requests
        if relevant:
            self._handoff_record("reuse_receipt_before", batch_id=meta.batch_ids[target["request_id"]],
                request_id=target["request_id"], state=self._reuse_state())
        result = super().update_connector_output(connector_output)
        if relevant:
            self._handoff_record("reuse_receipt_after", batch_id=meta.batch_ids[target["request_id"]],
                request_id=target["request_id"], state=self._reuse_state())
            if meta.batch_ids[target["request_id"]] == target["batch_id"]:
                self._reject_old("duplicate_after_old_terminal", False)
        return result
