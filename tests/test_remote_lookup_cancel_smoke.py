import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_lookup_cancel_smoke import deferred_requests


class DeferredMetricTests(unittest.TestCase):
    def test_only_deferred_waiting_requests_count(self):
        metrics = """vllm:num_requests_waiting_by_reason{engine="0",reason="capacity"} 3.0
vllm:num_requests_waiting_by_reason{engine="0",reason="deferred"} 1.0
vllm:num_requests_waiting_by_reason{engine="1",reason="deferred"} 2.0
"""
        self.assertEqual(deferred_requests(metrics), 3.0)

    def test_missing_metric_is_zero(self):
        self.assertEqual(deferred_requests("vllm:num_requests_waiting 1\n"), 0)


if __name__ == "__main__":
    unittest.main()
