from cachepilot_test_support.measurement import write, fixture
import copy
import hashlib
import json

from scripts.audit_replay_measurements import audit_cell, describe


def test_independent_quantile_interpolation():
    assert describe([0, 10, 20])["p95"] == 19
    assert describe([7])["p99"] == 7


def test_audit_rejects_tampered_timestamp_and_input(tmp_path):
    rows, _ = fixture(tmp_path)
    assert audit_cell(tmp_path)["passed"]
    rows[0]["chunk_arrivals_ms"][0] += 1
    rows[1]["prompt_sha256"] = "other-input"
    write(tmp_path / "requests.json", dict(mode="open", results=rows))
    report = audit_cell(tmp_path)
    assert not report["passed"]
    assert "0:stored_latency:ttft_ms" in report["errors"]
    assert "1:trace_mismatch:prompt_sha256" in report["errors"]


def test_audit_rejects_tampered_summary(tmp_path):
    _, summary = fixture(tmp_path)
    summary["ttft_ms"]["p95"] = 1
    write(tmp_path / "result.json", summary)
    assert "summary:ttft_ms:p95" in audit_cell(tmp_path)["errors"]


def test_audit_rejects_duplicate_events_and_hash(tmp_path):
    rows, _ = fixture(tmp_path)
    rows[1]["event_id"] = rows[0]["event_id"]
    rows[0]["output_text"] = "changed"
    write(tmp_path / "requests.json", dict(mode="open", results=rows))
    report = audit_cell(tmp_path)
    assert "event_ids" in report["errors"]
    assert "0:output_hash" in report["errors"]
