"""Deterministic synthetic measurement cell for audit tests."""
import copy
import hashlib
import json


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
