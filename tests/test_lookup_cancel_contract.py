"""Known-failing cancellation contract against the pinned LMCache adapter."""

import threading
import time
import unittest
from types import SimpleNamespace

try:
    from lmcache.integration.vllm.vllm_multi_process_adapter import (
        LMCacheMPSchedulerAdapter,
    )
except ModuleNotFoundError:
    LMCacheMPSchedulerAdapter = None


class PendingLookup:
    def __init__(self):
        self.acknowledged = threading.Event()
        self.wait_entered = threading.Event()

    def result(self, timeout=None):
        self.wait_entered.set()
        if not self.acknowledged.wait(timeout):
            raise TimeoutError("LOOKUP acknowledgement timed out")


class RequestClient:
    def __init__(self):
        self.end_submitted = threading.Event()

    def end_session(self, request_id):
        self.end_submitted.set()


@unittest.skipUnless(
    LMCacheMPSchedulerAdapter is not None,
    "LMCache is only installed in the GPU validation environment",
)
class LookupCancellationContractTests(unittest.TestCase):
    def test_current_cleanup_allows_end_before_lookup_ack(self):
        """Characterization only; the desired ordering is the opposite."""
        adapter = LMCacheMPSchedulerAdapter.__new__(LMCacheMPSchedulerAdapter)
        future = PendingLookup()
        client = RequestClient()
        healthy = threading.Event()
        healthy.set()
        adapter._health_events = {"server": healthy}
        adapter._server_urls = ["server"]
        adapter.req_clients = {"server": client}
        adapter._mq_timeout = 2.0
        adapter._pending_lookups = {"request"}
        adapter._unacked_lookups = {
            "request": SimpleNamespace(futures={"server": future},
                                       submitted_at=time.monotonic())
        }
        adapter._lookup_status = {}
        adapter._finished_lookup_results = {}
        adapter._per_server_hits = {}
        adapter._lookup_params = {}

        adapter.cleanup_lookup_result("request")
        started = threading.Event()
        errors = []

        def finish():
            started.set()
            try:
                adapter.end_session("request")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=finish, daemon=True)
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            ended_before_ack = client.end_submitted.wait(0.2)
        finally:
            future.acknowledged.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive(), "end_session did not complete")
        self.assertFalse(errors, errors)
        self.assertTrue(client.end_submitted.is_set(), "END_SESSION was not sent")
        self.assertTrue(ended_before_ack, "Pinned adapter behavior changed; reassess contract")


if __name__ == "__main__":
    unittest.main()
