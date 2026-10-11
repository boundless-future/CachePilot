import copy
import hashlib
import json

from scripts.audit_replay_measurements import audit_cell, describe


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path):
    events, rows = [], []
    for turn in (0, 1):
        event = dict(event_id=turn, session_id="s0", turn=turn, arrival_ms=turn * 100,
                     prompt_sha256="fixed-input", prompt_tokens=10, max_tokens=48)
        events.append(event)
        row = copy.deepcopy(event)
        row.update(error=None, http_status=200, scheduled_ms=turn * 100,
            sent_ms=turn * 100 + 1, finished_ms=turn * 100 + 30,
            chunk_arrivals_ms=[turn * 100 + 11, turn * 100 + 20],
            ttft_ms=10, ready_to_first_ms=11, e2e_ms=29, ready_to_end_ms=30,
            client_queue_ms=1, usage=dict(prompt_tokens=10, completion_tokens=48),
            output_text="hello", output_sha256=hashlib.sha256(b"hello").hexdigest())
        rows.append(row)
    summary = dict(requests=2, errors=0, output_tokens=96)
    for key, value in dict(ttft_ms=10, ready_to_first_ms=11, e2e_ms=29,
                           ready_to_end_ms=30, client_queue_ms=1).items():
        summary[key] = dict(p50=value, p95=value, p99=value, mean=value)
    summary.update(cold_ttft_ms=dict(p50=10, p95=10), reuse_ttft_ms=dict(p50=10, p95=10),
                   session_completion_ms=dict(mean=130, p95=130))
    write(tmp_path / "trace.json", dict(events=events))
    write(tmp_path / "requests.json", dict(mode="open", results=rows))
    write(tmp_path / "result.json", summary)
    return rows, summary


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
