"""Diagnostic-only receipt gate and shared GPU prefix cancellation tracing."""
import os
from pathlib import Path
import time

from scripts.protection_probe_connector import ProtectionProbeConnector


class GatedResult:
    def __init__(self, future, gate, record, clock=time.monotonic, timeout=120):
        self.future, self.gate, self.record, self.clock = future, Path(gate), record, clock
        self.deadline = clock() + timeout
        self.ready_seen = False
        self.open_seen = False

    def query(self):
        if self.clock() >= self.deadline:
            raise TimeoutError("Shared-cancel receipt gate expired; stop isolated engine")
        if not self.future.query():
            return False
        if not self.ready_seen:
            self.ready_seen = True
            self.record("shared_actual_ready")
        if not self.gate.exists():
            return False
        if not self.open_seen:
            self.open_seen = True
            self.record("shared_gate_opened")
        return True

    def result(self, *args, **kwargs):
        if not self.open_seen or self.clock() >= self.deadline:
            raise RuntimeError("Shared-cancel receipt accessed without live gate")
        actual = self.future.result(*args, **kwargs)
        if actual is not True:
            raise RuntimeError("Shared-cancel experiment requires actual STORE success")
        self.record("shared_result_delivered", actual_result=actual)
        return actual


class SharedCancelProbeConnector(ProtectionProbeConnector):
    def build_connector_meta(self, scheduler_output):
        metadata = super().build_connector_meta(scheduler_output)
        if not getattr(self, "_shared_target", None):
            for batch in metadata.cachepilot_handoffs_v1:
                if batch["source"] == "protection":
                    self._shared_target = batch
                    original = self._bounded.inflight[batch["request_id"]]
                    self._handoff_record("shared_target", **batch,
                        token_ids=list(original.metadata.op.token_ids[:batch["end"]]))
                    break
        return metadata

    def wait_for_save(self):
        batches = list(self._bound_handoffs)
        result = super().wait_for_save()
        if getattr(self, "_shared_gate_installed", False):
            return result
        for batch in batches:
            if batch["source"] != "protection":
                continue
            future = self.worker_adapter.store_futures.get(batch["request_id"])
            if future is None:
                raise RuntimeError("First protected batch has no real STORE future")

            def record(event, **fields):
                self._handoff_record(event, batch_id=batch["batch_id"],
                    request_id=batch["request_id"], **fields)

            self.worker_adapter.store_futures[batch["request_id"]] = GatedResult(
                future, os.environ["CACHEPILOT_SHARED_CANCEL_GATE"], record)
            self._shared_gate_installed = True
            record("shared_gate_installed", timeout_seconds=120)
            break
        return result

    def _shared_refs(self, scheduler, request):
        target = getattr(self, "_shared_target", None)
        if target is None or target["request_id"] not in self._bounded.inflight:
            return None
        manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
        allocated = {b.block_id for b in manager.req_to_blocks.get(request.request_id, ())}
        protected = {b.block_id for b in self._bounded.inflight[target["request_id"]].blocks}
        shared = sorted(allocated & protected)
        if not shared:
            return None
        return dict(batch_id=target["batch_id"], request_id=request.request_id,
            original_request_id=target["request_id"], status=request.status.name,
            shared_ids=shared, protected_ids=sorted(protected),
            refs={b: self._bounded.pool.blocks[b].ref_cnt for b in shared})

    def _snapshot(self, *args):
        result = super()._snapshot(*args)
        if len(args) == 2 and self._bounded is not None:
            scheduler, phase = args
            rows = [row for request in scheduler.requests.values()
                    if (row := self._shared_refs(scheduler, request)) is not None]
            signature = repr(rows)
            if signature != getattr(self, "_last_shared_refs", None):
                self._last_shared_refs = signature
                for row in rows:
                    self._handoff_record("shared_live_refs", phase=phase, **row)
        return result

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        from vllm.v1.core.sched.scheduler import Scheduler
        if getattr(Scheduler._free_blocks, "_cachepilot_shared_cancel", False):
            return
        original_free = Scheduler._free_blocks

        def free(scheduler, request):
            connector = getattr(scheduler, "connector", None)
            row = connector._shared_refs(scheduler, request) if isinstance(
                connector, SharedCancelProbeConnector) else None
            if row:
                connector._handoff_record("shared_free_before", **row)
            result = original_free(scheduler, request)
            if row:
                manager = scheduler.kv_cache_manager.coordinator.single_type_managers[0]
                connector._handoff_record("shared_free_after", **dict(row,
                    refs={b: connector._bounded.pool.blocks[b].ref_cnt for b in row["shared_ids"]}),
                    allocation_present=request.request_id in manager.req_to_blocks,
                    protection_inflight=row["original_request_id"] in connector._bounded.inflight,
                    manager_inflight=connector._lazy_offload_manager._requests.has_in_flight(
                        row["original_request_id"]))
            return result

        free._cachepilot_shared_cancel = True
        Scheduler._free_blocks = free

    def update_connector_output(self, connector_output):
        meta = connector_output.kv_connector_worker_meta
        target = getattr(self, "_shared_target", None)
        blocks = None
        if meta is not None and target and target["request_id"] in meta.completed_store_requests:
            blocks = self._bounded.inflight[target["request_id"]].blocks
            self._handoff_record("shared_receipt_before", batch_id=target["batch_id"],
                request_id=target["request_id"], refs={b.block_id: b.ref_cnt for b in blocks})
        result = super().update_connector_output(connector_output)
        if blocks is not None:
            self._handoff_record("shared_receipt_after", batch_id=target["batch_id"],
                request_id=target["request_id"], refs={b.block_id: b.ref_cnt for b in blocks},
                protection_inflight=target["request_id"] in self._bounded.inflight,
                manager_inflight=self._lazy_offload_manager._requests.has_in_flight(target["request_id"]))
        return result
