from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cancel_held_store_smoke import audit


class CancelHeldStoreAuditTests(unittest.TestCase):
    def rows(self):
        rows = [
            dict(event="block_pool_bound", free_blocks=100),
            dict(event="request_arrived", request_id="a"),
            dict(event="store_receipt_held", request_id="a"),
            dict(event="request_finished", request_id="a", status="FINISHED_ABORTED"),
            dict(event="held_store_result_delivered", request_id="a", actual_result=True),
            dict(event="scheduler_receipt_before", request_id="a", completed=1,
                 failed=False, orphaned=False, pinned_refs={"4": 1}),
            dict(event="scheduler_receipt_after", request_id="a", in_flight=False,
                 pinned_refs={"4": 0}),
            dict(event="scheduler_snapshot", free_blocks=100, registered_ids=[],
                 deferred_frees=0, tracked_refs={}),
        ]
        return [dict(monotonic_ns=i * 1000000, **r) for i, r in enumerate(rows)]

    def test_cancellation_drains_without_new_request(self):
        result = audit(self.rows())
        self.assertEqual(result["released_pin_references"], 1)
        self.assertEqual(result["receipt_after_cancel_ms"], 2)

    def test_early_receipt_or_normal_completion_is_not_cancellation_coverage(self):
        for index, field, value in [(3, "monotonic_ns", 6000000),
                                     (3, "status", "FINISHED_LENGTH_CAPPED"),
                                     (4, "actual_result", False)]:
            rows = self.rows()
            rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                audit(rows)

    def test_tick_request_invalidates_no_tick_claim(self):
        rows = self.rows()
        rows.append(dict(event="request_arrived", request_id="tick", monotonic_ns=4500000))
        with self.assertRaises(AssertionError):
            audit(rows)

    def test_incomplete_resource_release_fails(self):
        for field, value in [("free_blocks", 99), ("registered_ids", ["a"]),
                             ("deferred_frees", 1), ("tracked_refs", {"4": 1})]:
            rows = self.rows()
            rows[-1][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                audit(rows)

    def test_duplicate_batch_is_not_silently_ignored(self):
        rows = self.rows()
        rows.append(deepcopy(rows[2]))
        with self.assertRaises(AssertionError):
            audit(rows)


if __name__ == "__main__":
    unittest.main()
