"""Reject incomplete resource evidence in the async cancellation probe."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lifecycle_smoke import lifecycle_summary


class LifecycleSummaryTests(unittest.TestCase):
    def evidence(self):
        return [
            dict(event="scheduler_snapshot", pid=1, monotonic_ns=1,
                 free_blocks=909, requests=[], registered_ids=[], tracked_refs={}),
            dict(event="scheduler_snapshot", pid=1, monotonic_ns=2,
                 free_blocks=637, registered_ids=["r"], tracked_refs={"1": 1},
                 requests=[dict(request_id="r", status="WAITING_FOR_REMOTE_KVS",
                                block_ids=list(range(272)))]),
            dict(event="retrieve_delayed", monotonic_ns=3, request_ids=["r"]),
            dict(event="request_finished", pid=1, monotonic_ns=4,
                 request_id="r", status="FINISHED_ABORTED"),
            dict(event="retrieve_cancelled_before_submit", monotonic_ns=5, request_ids=["r"]),
            dict(event="worker_cancel_completion", monotonic_ns=6, request_ids=["r"]),
            dict(event="scheduler_finished_recving", monotonic_ns=7, request_ids=["r"]),
            dict(event="scheduler_snapshot", pid=1, monotonic_ns=8,
                 free_blocks=909, requests=[], registered_ids=[], tracked_refs={}),
        ]

    def summarize(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "lifecycle-1.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )
            return lifecycle_summary(path)

    def test_complete_resource_evidence_passes(self):
        result = self.summarize(self.evidence())
        self.assertEqual(result["free_blocks_after_completion"], 909)
        self.assertFalse(result["request_registered_after_completion"])

    def test_empty_queue_does_not_prove_release(self):
        rows = self.evidence()
        rows[-1].update(registered_ids=["r"], tracked_refs={"1": 1}, free_blocks=637)
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_missing_receipt_or_duplicate_receipt_is_rejected(self):
        rows = self.evidence()
        with self.assertRaises(AssertionError):
            self.summarize([row for row in rows if row["event"] != "scheduler_finished_recving"])
        rows.append(rows[-2])
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_free_block_count_must_recover(self):
        rows = self.evidence()
        rows[-1]["free_blocks"] = 908
        with self.assertRaises(AssertionError):
            self.summarize(rows)
