from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from scripts.late_store_receipt_connector import HeldStoreResult
except ModuleNotFoundError:
    HeldStoreResult = None


@unittest.skipUnless(HeldStoreResult, "Needs pinned vLLM/LMCache imports")
class HeldStoreReceiptTests(unittest.TestCase):
    def test_time_does_not_make_unfinished_dma_ready(self):
        original = Mock()
        original.query.return_value = False
        with patch("scripts.late_store_receipt_connector.time.monotonic", return_value=0):
            held = HeldStoreResult(original, 5, Mock())
        with patch("scripts.late_store_receipt_connector.time.monotonic", return_value=10):
            self.assertFalse(held.query())
        original.result.assert_not_called()

    def test_actual_failure_is_preserved_after_delay(self):
        original, report = Mock(), Mock()
        original.query.return_value = True
        original.result.return_value = False
        with patch("scripts.late_store_receipt_connector.time.monotonic", return_value=0):
            held = HeldStoreResult(original, 5, report)
            self.assertFalse(held.query())
            with self.assertRaises(RuntimeError):
                held.result()
        with patch("scripts.late_store_receipt_connector.time.monotonic", return_value=5):
            self.assertTrue(held.query())
            self.assertFalse(held.result())
        self.assertEqual(report.call_count, 2)


if __name__ == "__main__":
    unittest.main()
