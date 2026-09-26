"""Evidence must not silently pass because no comparisons were made."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_kv_probe import summarize_probe, compare_outputs


class ProbeEvidenceTests(unittest.TestCase):
    def row(self, layer):
        return dict(phase='after_retrieve', request_id='r',start=0,end=256,
                    token_prefix_sha256='digest',layers=[dict(layer='layer0',**layer)])

    def test_no_evidence_is_not_success(self):
        self.assertFalse(summarize_probe([])['covered_and_equal'])
        self.assertFalse(summarize_probe([self.row({})])['covered_and_equal'])

    def test_missing_reference_is_not_equality(self):
        row = self.row(dict(reference_present=False,bitwise_equal=True))
        self.assertFalse(summarize_probe([row])['covered_and_equal'])

    def test_mixed_equal_and_different_layers_fails(self):
        good = self.row(dict(reference_present=True,bitwise_equal=True))
        bad = self.row(dict(reference_present=True,bitwise_equal=False,sha256='different'))
        self.assertTrue(summarize_probe([good])['covered_and_equal'])
        self.assertFalse(summarize_probe([good,bad])['covered_and_equal'])

    def test_different_request_sets_fail(self):
        row = dict(event_id=1,turn=0,response={'choices':[{'text':'same'}]})
        with self.assertRaises(ValueError):compare_outputs([row],[])
        with self.assertRaises(ValueError):compare_outputs([row],[row,row])
        self.assertEqual(compare_outputs([row],[row])['mismatches'],0)


if __name__ == '__main__':unittest.main()
