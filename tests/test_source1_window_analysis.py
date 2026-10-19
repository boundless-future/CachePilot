import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analyze_source1_window import analyze_rows, source_reuse
from analyze_whole_step_identity import prefix_sha


def observation(serial, *, intact=True, safe=False, stage="policy_drain_before"):
    return dict(event="source1_window_snapshot", request_id="cmpl-cachepilot-trace-0001-0",
        observation=serial, stage=stage, all_pending_hashes_intact=intact,
        actionable_current_front=safe, pending=[], free_blocks=100,
        reserve_blocks=50, max_safe_protection_blocks=32)


class Source1WindowAnalysisTests(unittest.TestCase):
    def test_missing_observations_fails_closed(self):
        result = analyze_rows([])
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "no_source1_pending_observations")

    def test_safe_window_before_hash_loss_is_preserved(self):
        result = analyze_rows([observation(1, safe=True), observation(2, intact=False)])
        self.assertEqual(result["conclusion"], "safe_candidate_window_observed")
        self.assertEqual(result["safe_windows"][0]["observation"], 1)
        self.assertEqual(result["first_hash_loss"]["observation"], 2)

    def test_no_safe_window_is_not_mistaken_for_failure(self):
        result = analyze_rows([observation(1), observation(2, intact=False)])
        self.assertTrue(result["passed"])
        self.assertEqual(result["conclusion"], "no_safe_candidate_window_observed")

    def test_unrelated_request_rows_are_ignored(self):
        result = analyze_rows([dict(observation(1), request_id="other")])
        self.assertFalse(result["passed"])


class SourceReuseTests(unittest.TestCase):
    def setUp(self):
        self.trace = dict(events=[dict(event_id=i, prompt=[42] * 4400) for i in (1, 9)])

    def completed_store(self, start, end, serial):
        op = dict(event="worker_bound", role="WORKER", direction="STORE",
            request_id="cmpl-cachepilot-trace-0001-0", wire_id="source-wire",
            batch_id=f"batch-{serial}", start=start, end=end, block_ids=[[serial]],
            prefix_sha256=prefix_sha([42] * end), monotonic_ns=serial * 10)
        receipt = dict(op, event="scheduler_receipt", role="SCHEDULER",
                       failed=False, monotonic_ns=serial * 10 + 4)
        transfer = dict(request_id="source-wire", sequence=serial, kind="store",
                        blocks=[[serial]], event="transfer_submitted", monotonic_ns=serial * 10 + 1)
        return [op, receipt], [transfer,
            dict(transfer, event="terminal_seen", monotonic_ns=serial * 10 + 2),
            dict(transfer, event="transfer_retired", succeeded=True, monotonic_ns=serial * 10 + 3)]

    def test_contiguous_store_requires_original_receipt_and_native_success(self):
        handoffs, service = [], []
        for serial, (start, end) in enumerate(((0, 2048), (2048, 4096), (4096, 4352)), 1):
            h, s = self.completed_store(start, end, serial)
            handoffs.extend(h)
            service.extend(s)
        result = source_reuse(self.trace, handoffs, service)
        self.assertEqual(result["contiguous_saved_tokens"], 4352)
        self.assertFalse(result["cache_path_observed"])
        forged = [dict(row, batch_id="wrong") if row["event"] == "scheduler_receipt"
                  and row["end"] == 4096 else row for row in handoffs]
        self.assertEqual(source_reuse(self.trace, forged, service)["contiguous_saved_tokens"], 2048)
        incomplete = [row for row in service if row["event"] != "terminal_seen"]
        self.assertEqual(source_reuse(self.trace, handoffs, incomplete)["contiguous_saved_tokens"], 0)

    def test_saved_suffix_cannot_bridge_missing_prefix(self):
        handoffs, service = self.completed_store(2048, 4352, 1)
        result = source_reuse(self.trace, handoffs, service)
        self.assertEqual(len(result["successful_source_store_ranges"]), 1)
        self.assertEqual(result["contiguous_saved_tokens"], 0)

    def test_observed_hash_loss_precedes_completed_store(self):
        handoffs, service = self.completed_store(0, 4352, 1)
        handoffs.append(dict(event="source1_window_snapshot", monotonic_ns=12,
                             all_pending_hashes_intact=False))
        result = source_reuse(self.trace, handoffs, service)
        self.assertFalse(result["full_store_before_observed_hash_loss"])


if __name__ == "__main__":
    unittest.main()
