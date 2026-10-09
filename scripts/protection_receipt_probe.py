"""One protected batch: delay a real result or report failure after success.

Neither mode interrupts DMA. The failure mode is explicitly a receipt-path
diagnostic, not a real STORE execution failure.
"""
import os
import time

from scripts.protection_probe_connector import ProtectionProbeConnector


class ProbedResult:
    def __init__(self, future, mode, record, clock=time.monotonic):
        if mode not in ("delayed", "failed-after-success"):
            raise ValueError("Unsupported protected receipt probe")
        self.future, self.mode, self.record, self.clock = future, mode, record, clock
        self.deadline = clock() + (5 if mode == "delayed" else 0)
        self.ready_seen = False

    def query(self):
        if not self.future.query():
            return False
        if not self.ready_seen:
            self.ready_seen = True
            self.record("protected_actual_ready", remaining_seconds=max(0, self.deadline-self.clock()))
        return self.clock() >= self.deadline

    def result(self, *args, **kwargs):
        if self.clock() < self.deadline:
            raise RuntimeError("Protected result read before diagnostic deadline")
        actual = self.future.result(*args, **kwargs)
        if self.mode == "failed-after-success" and actual is not True:
            raise RuntimeError("Failure-receipt diagnostic requires actual STORE success")
        delivered = False if self.mode == "failed-after-success" else actual
        self.record("protected_result_delivered", actual_result=actual, delivered_result=delivered)
        return delivered


class ReceiptProbeConnector(ProtectionProbeConnector):
    def wait_for_save(self):
        batches = list(self._bound_handoffs)
        result = super().wait_for_save()
        if getattr(self, "_receipt_probe_injected", False):
            return result
        for batch in batches:
            if batch["source"] != "protection":
                continue
            rid = batch["request_id"]
            future = self.worker_adapter.store_futures.get(rid)
            if future is None:
                continue
            mode = os.environ["CACHEPILOT_PROTECTED_RECEIPT_PROBE"]
            def record(event, **fields):
                self._handoff_record(event, batch_id=batch["batch_id"], request_id=rid, mode=mode, **fields)
            self.worker_adapter.store_futures[rid] = ProbedResult(future, mode, record)
            self._receipt_probe_injected = True
            record("protected_receipt_probe_installed", delay_seconds=5 if mode == "delayed" else 0)
            break
        return result

    def _snapshot(self, *args):
        result = super()._snapshot(*args)
        if len(args) != 2 or self._bounded is None:
            return result
        state = []
        for rid, original in self._bounded.inflight.items():
            assert self._lazy_offload_manager._requests.has_in_flight(rid)
            refs = {b.block_id: b.ref_cnt for b in original.blocks}
            assert all(value >= 1 for value in refs.values())
            state.append((rid, tuple(sorted(refs.items()))))
        signature = tuple(state)
        if signature != getattr(self, "_last_protected_refs", None):
            self._last_protected_refs = signature
            self._handoff_record("protected_runtime_refs", phase=args[1],
                batches=[dict(request_id=rid, refs=dict(refs)) for rid, refs in state])
        return result
