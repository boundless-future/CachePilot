from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from l2_prefetch_cancel_smoke import audit_checked_release


class CheckedReleaseAuditTests(unittest.TestCase):
    def rows(self):
        return [dict(event="reclaim_release_result", unix_time=2, request_id="r", token=1,
                     succeeded_keys=["a", "b"], failed_keys=[], errors=[], notification_error=None),
                dict(event="reclaim_completed", unix_time=3, request_id="r", token=1,
                     released_objects=2, object_keys=["a", "b"])]

    def test_matching_results_pass(self):
        self.assertEqual(audit_checked_release(self.rows(), 1, 2)["succeeded_keys"], ["a", "b"])

    def test_wrong_key_or_job_or_order_cannot_pass(self):
        for field, value in [("succeeded_keys", ["c", "d"]), ("token", 2),
                             ("request_id", "another"), ("unix_time", 4)]:
            rows = self.rows()
            rows[0][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                audit_checked_release(rows, 1, 2)

    def test_partial_or_notification_failure_cannot_pass(self):
        for field, value in [("failed_keys", ["b"]), ("errors", [["b", "invalid"]]),
                             ("notification_error", "event sink"), ("succeeded_keys", ["a", "a"])]:
            rows = self.rows()
            rows[0][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                audit_checked_release(rows, 1, 2)

    def test_missing_or_duplicate_result_cannot_pass(self):
        for rows in [self.rows()[1:], self.rows() + [self.rows()[0]]]:
            with self.assertRaises(AssertionError):
                audit_checked_release(rows, 1, 2)

    def test_empty_bitmap_closes_without_unlock(self):
        done = self.rows()[1]
        done.update(object_keys=[], released_objects=0)
        self.assertFalse(audit_checked_release([done], 1, 0)["release_called"])
