"""Evidence checks for the worker-result failure diagnostic."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from retrieve_failure_smoke import summarize


class RetrieveFailureEvidenceTests(unittest.TestCase):
    def rows(self):
        def row(event, tick, **fields):
            return dict(event=event, monotonic_ns=tick, **fields)
        return [
            row("scheduler_snapshot", 1, registered_ids=[], free_blocks=909,
                requests=[], tracked_refs={}, deferred_frees=0),
            row("retrieve_submitted", 2, request_id="r", block_ids=[4, 5]),
            row("scheduler_snapshot", 3, registered_ids=["r"], free_blocks=907,
                requests=[dict(request_id="r", status="WAITING_FOR_REMOTE_KVS")],
                tracked_refs={"4": 1, "5": 1}, deferred_frees=0),
            row("retrieve_result_overridden", 4, request_id="r", actual_result=True),
            row("worker_load_errors", 5, block_ids=[4, 5]),
            row("worker_get_finished", 5, receiving=["r"]),
            row("scheduler_invalid_blocks", 6, block_ids=[4, 5], recompute=True),
            row("scheduler_invalid_handled", 7, failed_recving=["r"]),
            row("scheduler_finished_recving", 8, request_ids=["r"]),
            row("scheduler_snapshot", 9, registered_ids=["r"], free_blocks=907,
                requests=[dict(request_id="r", status="WAITING_FOR_REMOTE_KVS",
                               computed_tokens=0, block_ids=[4, 5])],
                tracked_refs={}, deferred_frees=0),
            row("scheduler_snapshot", 10, registered_ids=["r"], free_blocks=907,
                requests=[dict(request_id="r", status="RUNNING", computed_tokens=2048,
                               block_ids=[4, 5])], tracked_refs={}, deferred_frees=0),
            row("request_finished", 11, request_id="r", status="FINISHED_LENGTH_CAPPED"),
            row("scheduler_snapshot", 12, registered_ids=[], free_blocks=909,
                requests=[], tracked_refs={}, deferred_frees=0),
        ]

    def summarize(self, rows, mode="receipt-only"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "retrieve-failure-1.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows))
            return summarize(path, mode=mode)

    def server_reject_rows(self):
        rows = self.rows()
        rows.insert(1, dict(event="retrieve_payload_invalidated", monotonic_ns=2,
                            request_id="r", original_block_counts=[2],
                            submitted_block_counts=[0]))
        for row in rows[2:]:
            row["monotonic_ns"] += 1
        rows[4]["event"] = "server_retrieve_result"
        rows[4]["actual_result"] = False
        return rows

    def test_complete_path(self):
        self.assertEqual(self.summarize(self.rows())["failed_blocks"], 2)

    def test_real_transfer_must_have_succeeded(self):
        rows = self.rows()
        rows[3]["actual_result"] = False
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_missing_scheduler_error_or_resource_release_fails(self):
        rows = self.rows()
        with self.assertRaises(AssertionError):
            self.summarize([row for row in rows if row["event"] != "scheduler_invalid_blocks"])
        rows[-1]["free_blocks"] = 908
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_worker_completion_must_be_reported_once(self):
        rows = self.rows()
        with self.assertRaises(AssertionError):
            self.summarize([row for row in rows if row["event"] != "worker_get_finished"])
        with self.assertRaises(AssertionError):
            self.summarize(rows + [next(row for row in rows
                                        if row["event"] == "worker_get_finished")])

    def test_server_rejection_requires_real_false_future(self):
        rows = self.server_reject_rows()
        self.assertEqual(self.summarize(rows, "server-reject")["failed_blocks"], 2)
        rows[4]["actual_result"] = True
        with self.assertRaises(AssertionError):
            self.summarize(rows, "server-reject")

    def test_server_rejection_requires_underflow_proof(self):
        rows = self.server_reject_rows()
        rows[1]["submitted_block_counts"] = [2]
        with self.assertRaises(AssertionError):
            self.summarize(rows, "server-reject")
        rows = self.server_reject_rows()
        rows.pop(1)
        with self.assertRaises(AssertionError):
            self.summarize(rows, "server-reject")


if __name__ == "__main__":
    unittest.main()
