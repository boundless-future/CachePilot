"""Diagnostic: defer observation of real STORE results, never forge outcomes.

This prolongs scheduler ownership, not GPU DMA. The ready event explicitly
records when underlying work has already completed during the held receipt.
"""
import os
import time

from scripts.preemption_connector import PreemptionConnector


class HeldStoreResult:
    def __init__(self, future, delay, report):
        self.future, self.report = future, report
        self.ready_after = time.monotonic() + delay
        self.observed_ready = False

    def query(self):
        actual = self.future.query()
        if not actual:
            return False
        if not self.observed_ready:
            self.observed_ready = True
            self.report("actual_store_ready", remaining_hold_seconds=max(0, self.ready_after-time.monotonic()))
        return time.monotonic() >= self.ready_after

    def result(self, *args, **kwargs):
        if time.monotonic() < self.ready_after:
            raise RuntimeError("Result requested before held receipt was ready")
        result = self.future.result(*args, **kwargs)
        self.report("held_store_result_delivered", actual_result=result)
        return result


class LateStoreReceiptConnector(PreemptionConnector):
    def wait_for_save(self):
        result = super().wait_for_save()
        delay = float(os.environ.get("CACHEPILOT_STORE_RECEIPT_DELAY", "5"))
        if not 0 < delay <= 30:
            raise ValueError("Receipt delay must be in (0, 30]")
        for request_id, future in list(self.worker_adapter.store_futures.items()):
            if isinstance(future, HeldStoreResult):
                continue
            token = getattr(self, "_held_batch", 0) + 1
            self._held_batch = token
            self._observe("store_receipt_held", request_id=request_id, batch=token, delay_seconds=delay)

            def report(event, request_id=request_id, token=token, **fields):
                self._observe(event, request_id=request_id, batch=token, **fields)

            self.worker_adapter.store_futures[request_id] = HeldStoreResult(future, delay, report)
        return result
