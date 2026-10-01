from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from natural_preemption_smoke import audit, audit_owned_copy_windows, audit_receipts


class NaturalPreemptionAuditTests(unittest.TestCase):
    def rows(self):
        rows = [
            dict(event="block_pool_bound", free_blocks=454),
            dict(event="preempt_before", request_id="a", status="RUNNING", num_preemptions=0,
                 store_inflight=True),
            dict(event="preempt_after", request_id="a", status="PREEMPTED", num_preemptions=1,
                 computed_tokens=0),
            dict(event="request_finished", request_id="a", status="FINISHED_LENGTH_CAPPED", num_preemptions=1),
            dict(event="request_finished", request_id="b", status="FINISHED_LENGTH_CAPPED", num_preemptions=0),
            dict(event="store_receipt", failed=[]),
            dict(event="scheduler_snapshot", free_blocks=454, registered_ids=[], deferred_frees=0, tracked_refs={}),
        ]
        return [dict(monotonic_ns=i, **r) for i, r in enumerate(rows)]

    def test_complete_lifecycle(self):
        self.assertEqual(audit(self.rows(), 2)["preemptions_with_store_inflight"], 1)

    def test_missing_pair_or_wrong_generation_fails(self):
        for field, value in (("request_id", "b"), ("num_preemptions", 3)):
            rows = self.rows()
            rows[2][field] = value
            with self.assertRaises(AssertionError):
                audit(rows, 2)

    def test_incomplete_free_state_fails(self):
        for field, value in (("free_blocks", 453), ("registered_ids", ["a"]),
                             ("deferred_frees", 1), ("tracked_refs", {1: 1})):
            rows = self.rows()
            rows[-1][field] = value
            with self.assertRaises(AssertionError):
                audit(rows, 2)

    def test_failed_store_or_duplicate_finished_fails(self):
        rows = self.rows()
        rows[-2]["failed"] = ["a"]
        with self.assertRaises(AssertionError):
            audit(rows, 2)
        rows = self.rows()
        rows[4]["request_id"] = "a"
        with self.assertRaises(AssertionError):
            audit(rows, 2)

    def test_orphaned_receipt_must_release_each_pin_once(self):
        before = dict(event="scheduler_receipt_before", monotonic_ns=1, request_id="r",
                      completed=1, failed=False, orphaned=True, pinned_refs={"1": 2, "2": 1})
        after = dict(event="scheduler_receipt_after", monotonic_ns=2, request_id="r",
                     in_flight=False, pinned_refs={"1": 1, "2": 0})
        self.assertEqual(audit_receipts([before, after])["orphaned_receipts"], 1)
        after["pinned_refs"]["1"] = 2
        with self.assertRaises(AssertionError):
            audit_receipts([before, after])

    def test_unmatched_receipt_and_nonterminal_count_fail(self):
        before = dict(event="scheduler_receipt_before", monotonic_ns=1, request_id="r",
                      completed=0, failed=False, orphaned=True, pinned_refs={"1": 1})
        with self.assertRaises(AssertionError):
            audit_receipts([before])
        after = dict(event="scheduler_receipt_after", monotonic_ns=2, request_id="r",
                     in_flight=False, pinned_refs={"1": 0})
        with self.assertRaises(AssertionError):
            audit_receipts([before, after])

    def copy_rows(self):
        identity = "cp1:7:1:" + "a" * 32 + ":r:original"
        return [dict(event="diagnostic_copy_enqueued", request_id=identity,
                     sequence=3, kind="store", monotonic_ns=10, host_exception=False),
                dict(event="preempt_before", request_id="r:original",
                     monotonic_ns=20, store_inflight=True),
                dict(event="terminal_seen", request_id=identity, sequence=3,
                     kind="store", monotonic_ns=25),
                dict(event="transfer_retired", request_id=identity, sequence=3,
                     kind="store", monotonic_ns=30, succeeded=True)]

    def test_owned_copy_requires_same_original_pending_transfer(self):
        self.assertEqual(audit_owned_copy_windows(self.copy_rows(), 1)[0]["preemptions_ns"], [20])
        for field, value in (("request_id", "other"), ("monotonic_ns", 31),
                             ("store_inflight", False)):
            rows = self.copy_rows()
            rows[1][field] = value
            with self.assertRaises(AssertionError):
                audit_owned_copy_windows(rows, 1)

    def test_wrong_sequence_or_duplicate_terminal_cannot_cover_window(self):
        rows = self.copy_rows()
        rows[3]["sequence"] = 4
        with self.assertRaises(AssertionError):
            audit_owned_copy_windows(rows, 1)
        rows = self.copy_rows()
        rows.append(dict(rows[3]))
        with self.assertRaises(AssertionError):
            audit_owned_copy_windows(rows, 1)

    def test_preemption_after_native_marker_does_not_cover_window(self):
        rows = self.copy_rows()
        rows[1]["monotonic_ns"] = 27
        with self.assertRaises(AssertionError):
            audit_owned_copy_windows(rows, 1)


if __name__ == "__main__":
    unittest.main()
