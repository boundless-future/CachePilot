import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_lookup_timeline import summarize


class LookupTimelineTests(unittest.TestCase):
    def test_identifies_dropped_ack_before_end_session(self):
        events = [
            dict(request_id="warmup", event="rpc_submit", method="lookup",
                 unix_time=1, monotonic_ns=1),
            dict(request_id="target", event="rpc_submit", method="lookup",
                 unix_time=11, monotonic_ns=2),
            dict(request_id="target", event="adapter_before",
                 method="cleanup_lookup_result", unix_time=12,
                 monotonic_ns=3, state={"unacked_urls": ["server"]}),
            dict(request_id="target", event="adapter_after",
                 method="cleanup_lookup_result", unix_time=12.1,
                 monotonic_ns=4, state={"unacked_urls": []}),
            dict(request_id="target", event="rpc_submit", method="end_session",
                 unix_time=12.2, monotonic_ns=5),
        ]
        status = {"l1_read_locked": 17, "active_prefetch_jobs": 1}
        result = dict(connector="LookupTimelineConnector", passed=False,
                      timestamps_unix=dict(server_paused=10, server_resumed=13,
                                           client_disconnected=11.5),
                      cache_status_after_cancel=status,
                      cache_status_after_engine_stop=status)
        summary = summarize(events, result)
        self.assertEqual(summary["request_id"], "target")
        self.assertTrue(summary["cleanup_dropped_unacked_before_end"])
        self.assertEqual(summary["status_query_count"], 0)

        events.insert(-1, dict(request_id="target", event="rpc_submit",
                               method="query_prefetch_status", unix_time=12.15,
                               monotonic_ns=4.5))
        self.assertFalse(summarize(events, result)["cleanup_dropped_unacked_before_end"])


if __name__ == "__main__":
    unittest.main()
