from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_preemption_kv import audit_probe_sources


class PreemptionKVAuditTests(unittest.TestCase):
    def evidence(self):
        common = dict(start=0, end=256, token_prefix_sha256="key", block_ids=[1, 2])
        rows = [dict(**common, phase="before_store", monotonic_ns=1,
                     request_id="source", layers=[]),
                dict(**common, phase="after_retrieve", monotonic_ns=3,
                     request_id="restored", layers=[dict(layer="L", reference_present=True, bitwise_equal=True)])]
        receipts = [dict(event="scheduler_receipt_before", monotonic_ns=2,
                         request_id="source", orphaned=True, failed=False, pinned_refs={"1": 1,"2": 1})]
        return rows, receipts

    def test_equal_restored_orphan_batch_is_covered(self):
        self.assertTrue(audit_probe_sources(*self.evidence())["passed"])

    def test_no_orphan_or_wrong_blocks_does_not_prove_requested_path(self):
        for field, value in [("orphaned", False), ("pinned_refs", {"3": 1}),
                             ("failed", True), ("monotonic_ns", 4)]:
            rows, receipts = self.evidence()
            receipts[0][field] = value
            self.assertFalse(audit_probe_sources(rows, receipts)["passed"])

    def test_no_retrieval_or_mismatched_content_fails(self):
        rows, receipts = self.evidence()
        self.assertFalse(audit_probe_sources(rows[:1], receipts)["passed"])
        rows[1]["layers"][0].update(bitwise_equal=False, sha256="changed")
        self.assertFalse(audit_probe_sources(rows, receipts)["passed"])

    def test_an_intervening_store_does_not_prove_original_batch_persisted(self):
        rows, receipts = self.evidence()
        rows.insert(1, dict(rows[0], monotonic_ns=2.5))
        audit = audit_probe_sources(rows, receipts)
        self.assertTrue(audit["kv"]["covered_and_equal"])
        self.assertEqual(len(audit["orphan_source_chunks"]), 1)
        self.assertFalse(audit["unambiguous_orphan_source_chunks"])
        self.assertFalse(audit["passed"])
