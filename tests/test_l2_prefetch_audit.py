"""The L2 experiment must reject false positives in its lifecycle evidence."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from l2_prefetch_cancel_smoke import audit_events


class L2AuditTests(unittest.TestCase):
    def setUp(self):
        keys = [f"object-{i}" for i in range(17)]
        def row(event, timestamp, **fields):
            return dict(event=event, unix_time=timestamp, pid=1, task_id=2,
                        object_keys=list(keys), **fields)
        self.rows = [
            row("gate_entered", 1, phase="lookup"),
            row("reclaim_owned", 3, token=7, request_id="r", prefetch_request_id=4),
            row("reclaim_pending", 3.1, token=7, request_id="r"),
            row("gate_released", 4, phase="lookup"),
            row("gate_entered", 5, phase="load"),
            row("gate_released", 6, phase="load"),
            row("io_returned", 7, phase="load"),
            row("reclaim_completed", 8, token=7, request_id="r", released_objects=17, readers=1),
        ]

    def test_valid_pending_l2_reclaim(self):
        self.assertTrue(audit_events(self.rows, 2, True)["keys_match_load"])

    def test_follow_up_io_is_not_confused_with_cancelled_load(self):
        follow_up = []
        for row in self.rows:
            if row["event"].startswith(("gate_", "io_")):
                later = copy.deepcopy(row)
                later["unix_time"] += 20
                later["task_id"] += 2
                follow_up.append(later)
        self.assertTrue(audit_events(self.rows + follow_up, 2, True)["keys_match_load"])

    def test_wrong_objects_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[-1]["object_keys"][-1] = "different-object"
        with self.assertRaises(AssertionError):
            audit_events(rows, 2, True)

    def test_short_read_only_retained_prefix_is_reclaimed(self):
        rows = copy.deepcopy(self.rows)
        rows[-1]["released_objects"] = 8
        rows[-1]["object_keys"] = rows[-1]["object_keys"][:8]
        self.assertEqual(audit_events(rows, 2, True, 8)["released_objects"], 8)
        with self.assertRaises(AssertionError):
            audit_events(self.rows, 2, True, 8)

    def test_early_unlock_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[-1]["unix_time"] = 5.5
        with self.assertRaises(AssertionError):
            audit_events(rows, 2, True)

    def test_l1_only_or_missing_pending_not_l2_evidence(self):
        for change in ("l1", "pending"):
            rows = copy.deepcopy(self.rows)
            if change == "l1":
                rows[1]["prefetch_request_id"] = -1
            else:
                rows.pop(2)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                audit_events(rows, 2, True)

    def test_timeout_rejected(self):
        with self.assertRaises(AssertionError):
            audit_events(self.rows + [dict(event="gate_timeout")], 2, True)


if __name__ == "__main__":
    unittest.main()
