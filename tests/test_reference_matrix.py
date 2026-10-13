import json
from pathlib import Path

import pytest

from scripts.summarize_reference_matrix import collect, digest
from cachepilot_test_support.measurement import fixture as timing_fixture


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def native_matrix(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    cell = tmp_path / "native-r0"
    cell.mkdir()
    rows, result = timing_fixture(cell)
    trace = json.loads((cell / "trace.json").read_text())
    signature = dict(trace_sha256=digest(trace), source_sha256={"runner": "frozen"}, kv_cache_bytes=10)
    case = dict(kind="native")
    write(tmp_path / "plan.json", dict(cases={"native": case}, repeats=1, signature=signature,
                                     selection={}, stage="pilot"))
    write(cell / "config.json", {"native": True})
    write(cell / "launch-command.json", ["bash", "serve.sh", "baseline", "--enforce-eager"])
    write(cell / "run-manifest.json", dict(**signature, mode="native", repeat=0))
    write(cell / "reference-validation.json", dict(passed=True, signature=signature, case=case))
    write(cell / "requests.json", dict(mode="open", results=rows, trace_sha256=signature["trace_sha256"]))
    result.update(passed=True, mode="native", repeat=0, engine_returncode=0, mp_service_started=False,
                  trace_sha256=signature["trace_sha256"], vllm_delta={
                      "vllm:request_queue_time_seconds_sum": .01,
                      "vllm:request_queue_time_seconds_count": 2})
    write(cell / "result.json", result)
    (cell / "vllm.log").write_text("INFO clean exit\n")
    return tmp_path


def test_native_audit_uses_inapplicable_instead_of_fake_cpu_metrics(native_matrix):
    report = collect(native_matrix)
    assert report["matrix_complete"]
    assert "H2D/D2H" in report["rows"][0]["inapplicable"]
    assert "h2d_bytes" not in report["aggregates"]["native"]


@pytest.mark.parametrize("change", ["exit", "source", "trace", "log", "timestamp"])
def test_pass_flags_do_not_override_corrupted_evidence(native_matrix, change):
    cell = native_matrix / "native-r0"
    path = cell / {"exit": "result.json", "source": "run-manifest.json",
                   "trace": "trace.json", "log": "vllm.log", "timestamp": "requests.json"}[change]
    if change == "log":
        path.write_text("ERROR request failed\n")
    else:
        data = json.loads(path.read_text())
        if change == "exit":
            data["engine_returncode"] = -9
        elif change == "source":
            data["source_sha256"] = {"runner": "changed"}
        elif change == "trace":
            data["events"][0]["arrival_ms"] = 8
        else:
            data["results"][0]["chunk_arrivals_ms"][0] += 1
        write(path, data)
    report = collect(native_matrix)
    assert not report["matrix_complete"] and report["aggregates"] == {}


def test_partial_references_do_not_publish_partial_mean(native_matrix):
    path = native_matrix / "plan.json"
    plan = json.loads(path.read_text()); plan["repeats"] = 3
    write(path, plan)
    report = collect(native_matrix)
    assert report["passed_cells"] == 1 and report["expected_cells"] == 3
    assert not report["aggregates"]
