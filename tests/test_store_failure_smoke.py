"""Require both failed and completed STORE receipts and released blocks."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from store_failure_smoke import summarize


class StoreFailureEvidenceTests(unittest.TestCase):
    def rows(self):
        def row(event, tick, **fields):
            return dict(event=event, monotonic_ns=tick, **fields)
        return [
            row("block_pool_bound", 1, free_blocks=909),
            row("store_submitted", 2, request_id="r"),
            row("store_result_overridden", 3, request_id="r", actual_result=True),
            row("worker_store_receipt", 4, completed={"r": 1}, failed=["r"]),
            row("scheduler_store_receipt_before", 5, request_id="r",
                completed=1, failed=True, in_flight=True, pending=True,
                pinned_refs={"4": 2, "5": 2},
                free_blocks=900),
            row("scheduler_store_receipt_after", 6, request_id="r",
                in_flight=False, pending=False, free_blocks=905,
                pinned_refs={"4": 1, "5": 1}),
            row("request_finished", 7, request_id="r", status="FINISHED_LENGTH_CAPPED"),
            row("scheduler_snapshot", 8, registered_ids=[], free_blocks=909,
                deferred_frees=0),
        ]

    def summarize(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "store-failure-1.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows))
            return summarize(path)

    def test_complete_receipt(self):
        self.assertEqual(self.summarize(self.rows())["final_free_blocks"], 909)

    def test_failed_flag_and_completion_are_both_required(self):
        rows = self.rows()
        rows[3]["failed"] = []
        with self.assertRaises(AssertionError):
            self.summarize(rows)
        rows = self.rows()
        rows[3]["completed"] = {}
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_pending_and_pins_must_clear(self):
        rows = self.rows()
        rows[5]["pending"] = True
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_receipt_must_release_each_pin_once(self):
        rows = self.rows()
        rows[5]["pinned_refs"]["4"] = 2
        with self.assertRaises(AssertionError):
            self.summarize(rows)
        rows = self.rows()
        rows[-1]["free_blocks"] = 908
        with self.assertRaises(AssertionError):
            self.summarize(rows)


if __name__ == "__main__":
    unittest.main()
