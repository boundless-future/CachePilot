"""Expected ordering against the real pinned scheduler adapter and futures."""
from pathlib import Path
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lookup_ordering_connector import install_ordering

try:
    from lmcache.integration.vllm.vllm_multi_process_adapter import LMCacheMPSchedulerAdapter
    from lmcache.v1.multiprocess.futures import MessagingFuture
except ModuleNotFoundError:
    LMCacheMPSchedulerAdapter = None


@unittest.skipUnless(LMCacheMPSchedulerAdapter, "Needs pinned LMCache environment")
class LookupOrderingTests(unittest.TestCase):
    def setUp(self):
        a = LMCacheMPSchedulerAdapter.__new__(LMCacheMPSchedulerAdapter)
        healthy = threading.Event()
        healthy.set()
        self.end = threading.Event()
        a._health_events = {"server": healthy}
        a._server_urls = ["server"]
        client = Mock()
        client.end_session.side_effect = lambda request_id: self.end.set()
        a.req_clients = {"server": client}
        a._mq_timeout = 2.0
        a._pending_lookups = {"r"}
        a._unacked_lookups = {}
        a._lookup_status = {}
        a._finished_lookup_results = {"r": 17}
        a._per_server_hits = {"r": {}}
        a._lookup_params = {"r": {}}
        self.a = a
        install_ordering(a)

    def run_waiting_end(self, future):
        entered = threading.Event()
        original = future.wait
        def wait(timeout=None):
            entered.set()
            return original(timeout)
        future.wait = wait
        errors = []
        def end():
            try:
                self.a.end_session("r")
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=end, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.end.is_set())
        finally:
            future.set_result(None)
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertTrue(self.end.is_set())

    def test_cleanup_preserves_ack_end_waits_for_it(self):
        future = MessagingFuture()
        self.a._unacked_lookups["r"] = SimpleNamespace(futures={"server": future}, submitted_at=time.monotonic())
        self.a.cleanup_lookup_result("r")
        self.a.cleanup_lookup_result("r")
        self.assertFalse(self.a._pending_lookups)
        self.assertFalse(self.a._finished_lookup_results)
        self.run_waiting_end(future)
        self.assertFalse(self.a._unacked_lookups)

    def test_cleanup_preserves_already_submitted_status(self):
        future = MessagingFuture()
        self.a._lookup_status["r"] = {"server": (future, time.monotonic())}
        self.a.cleanup_lookup_result("r")
        self.run_waiting_end(future)
        self.assertFalse(self.a._lookup_status)

    def test_no_pending_future_does_not_add_state(self):
        self.a.cleanup_lookup_result("r")
        self.a.end_session("r")
        self.assertTrue(self.end.is_set())
        self.assertFalse(self.a._unacked_lookups)
        self.assertFalse(self.a._lookup_status)

    def test_timeout_still_drops_ack_known_boundary(self):
        self.a._mq_timeout = 0.01
        self.a._mark_lookup_timed_out = Mock()
        future = MessagingFuture()
        self.a._unacked_lookups["r"] = SimpleNamespace(futures={"server": future}, submitted_at=time.monotonic())
        self.a.cleanup_lookup_result("r")
        self.a.end_session("r")
        self.assertFalse(self.end.is_set())
        self.a._mark_lookup_timed_out.assert_called_once_with("server")
        self.assertFalse(self.a._unacked_lookups)  # Characterization, not fixed.


if __name__ == "__main__":
    unittest.main()
