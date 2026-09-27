"""Require real preemption and settled resource evidence for the GPU probe."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from preemption_smoke import preempted_completion, preemption_summary


class PreemptionSummaryTests(unittest.TestCase):
    def evidence(self):
        return [
            dict(event="scheduler_snapshot", monotonic_ns=1, free_blocks=909,
                 registered_ids=[], tracked_refs={}, deferred_frees=0),
            dict(event="preempt_before", monotonic_ns=2, request_id="r",
                 status="RUNNING", num_preemptions=0, block_ids=[1, 2],
                 free_blocks=907, computed_tokens=32),
            dict(event="preempt_after", monotonic_ns=3, request_id="r",
                 status="PREEMPTED", num_preemptions=1, computed_tokens=0,
                 registered=True, free_blocks=909),
            dict(event="request_finished", monotonic_ns=4, request_id="r",
                 status="FINISHED_LENGTH_CAPPED", num_preemptions=1),
            dict(event="blocks_freed", monotonic_ns=5, request_id="r",
                 registered=False, allocation_present=False, free_blocks=909),
            dict(event="scheduler_snapshot", monotonic_ns=6, free_blocks=909,
                 registered_ids=[], tracked_refs={}, deferred_frees=0),
        ]

    def summarize(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "preemption-1.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            return preemption_summary(path)

    def test_complete_evidence_passes_without_claiming_store(self):
        result = self.summarize(self.evidence())
        self.assertEqual(result["final_free_blocks"], 909)
        self.assertEqual(result["completed_store_receipts"], 0)

    def test_http_success_without_preemption_cannot_pass(self):
        with self.assertRaises(AssertionError):
            self.summarize([self.evidence()[0]])

    def test_unreleased_resources_cannot_pass(self):
        for change in (dict(registered_ids=["r"]), dict(tracked_refs={"1": 1}),
                       dict(deferred_frees=1), dict(free_blocks=908)):
            with self.subTest(change=change):
                rows = self.evidence()
                rows[-1].update(change)
                with self.assertRaises(AssertionError):
                    self.summarize(rows)

    def test_wrong_identity_duplicate_or_wrong_status_cannot_pass(self):
        for index, change in ((2, dict(request_id="other")),
                              (2, dict(monotonic_ns=1)),
                              (3, dict(status="FINISHED_ABORTED")),
                              (3, dict(num_preemptions=0)),
                              (4, dict(allocation_present=True))):
            with self.subTest(change=change):
                rows = self.evidence()
                rows[index].update(change)
                with self.assertRaises(AssertionError):
                    self.summarize(rows)
        rows = self.evidence()
        rows.append(rows[1])
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_failed_store_cannot_pass(self):
        rows = self.evidence()
        rows.append(dict(event="store_receipt", monotonic_ns=7,
                         completed=[], failed=["r"]))
        with self.assertRaises(AssertionError):
            self.summarize(rows)

    def test_earlier_idle_snapshot_does_not_hide_later_leak(self):
        rows = self.evidence()
        rows.append(dict(rows[-1], monotonic_ns=7, tracked_refs={"1": 1},
                         free_blocks=908))
        with self.assertRaises(AssertionError):
            self.summarize(rows)


class PreemptionStreamTests(unittest.TestCase):
    def responses(self, done=True):
        stream = Mock()
        rows = [b'data: {"choices":[{"text":"a","finish_reason":null}]}',
                b'data: {"choices":[{"text":"b","finish_reason":"length"}]}',
                b'data: {"choices":[],"usage":{"completion_tokens":32}}']
        if done:
            rows.append(b"data: [DONE]")
        stream.iter_lines.return_value = rows
        reset = Mock(status_code=500)
        reset.json.return_value = {"error": "reset failed"}
        return stream, reset

    def test_reset_error_keeps_stream_evidence_and_closes_connection(self):
        stream, reset = self.responses()
        with patch("preemption_smoke.requests.post", side_effect=[stream, reset]) as post:
            result = preempted_completion("http://localhost", "m", "p", 32)
        self.assertEqual(result["text"], "ab")
        self.assertEqual(result["reset"]["http_status"], 500)
        self.assertEqual(result["usage"]["completion_tokens"], 32)
        self.assertEqual(post.call_count, 2)
        stream.close.assert_called_once()

    def test_truncated_stream_is_rejected(self):
        stream, reset = self.responses(done=False)
        with patch("preemption_smoke.requests.post", side_effect=[stream, reset]):
            with self.assertRaisesRegex(AssertionError, "without.*DONE"):
                preempted_completion("http://localhost", "m", "p", 32)
        stream.close.assert_called_once()
