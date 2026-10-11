import copy
import json
from pathlib import Path

from scripts.summarize_policy_matrix import canonical_hash, collect


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def fixture(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    trace = {"events": [{"event_id": "e0", "max_tokens": 48}]}
    signature = dict(trace_sha256=canonical_hash(trace), source_sha256={"runner": "frozen"},
                     kv_cache_bytes=2684354560, idle_grace_seconds=15.0)
    write(tmp_path / "plan.json", dict(stage="pilot", signature=signature, repeats=1))
    state = dict(is_healthy=True, active_sessions=0, registered_gpu_ids=[], storage_manager={})
    for section, names in {
        "l1_manager": ("read_locked_count", "write_locked_count", "temporary_count"),
        "store_controller": ("pending_keys_count", "in_flight_task_count"),
        "prefetch_controller": ("in_flight_request_count", "pending_queue_size", "lookup_phase_count",
            "load_phase_count", "completed_results_count", "submission_queue_size"),
    }.items():
        state["storage_manager"][section] = dict.fromkeys(names, 0)
    state.update(dict.fromkeys(("owned_jobs", "owned_controller_jobs", "owned_transfers",
        "owned_deferred_contexts", "owned_failed", "owned_read_pins", "owned_store_batches", "active_prefetch_jobs"), 0))
    for index, mode in enumerate(("zero", "one", "two")):
        cell = tmp_path / f"{mode}-r0"
        summary = dict(shutdown_closed=True, committed_batches=int(index > 0), receipts=int(index > 0),
                       failed_receipts=0, peak_pinned_blocks=index * 16, peak_inflight_blocks=index * 16,
                       events={"idle_pending_dropped": 1}, raw=[])
        result = dict(passed=True, mode=mode, repeat=0, trace_sha256=signature["trace_sha256"],
            protection_summary=summary, status_before_engine_stop=copy.deepcopy(state),
            status_after_engine_stop=copy.deepcopy(state), h2d_bytes=1024, d2h_bytes=2048,
            vllm_delta={"vllm:request_queue_time_seconds_sum": .01,
                        "vllm:request_queue_time_seconds_count": 1})
        for key in ("ttft_ms", "reuse_ttft_ms", "ready_to_first_ms", "ready_to_end_ms",
                    "session_completion_ms", "client_queue_ms"):
            result[key] = {"p50": 1, "p95": 2, "p99": 3}
        write(cell / "result.json", result)
        write(cell / "trace.json", trace)
        write(cell / "run-manifest.json", dict(mode=mode, kv_cache_bytes=signature["kv_cache_bytes"],
            source_sha256=signature["source_sha256"], trace_sha256=signature["trace_sha256"]))
        write(cell / "idle-validation.json", dict(passed=True, signature=signature))
        write(cell / "requests.json", dict(trace_sha256=signature["trace_sha256"], results=[
            dict(event_id="e0", error=None, usage={"completion_tokens": 48}, turn=4,
                 ttft_ms=2, output_sha256="output")]))
        write(cell / "config.json", dict(kv_connector="IdleFinalizeConnector",
            kv_connector_module_path="scripts.idle_finalize_connector", kv_connector_extra_config={
                "cachepilot.idle_finalize": True, "cachepilot.idle_finalize_grace_seconds": 15.0,
                "cachepilot.protection.max_chunks": index}))
        (cell / "vllm.log").write_text("")
        (cell / "lmcache.log").write_text("")


def test_independent_audit_rejects_retained_owner_despite_pass_flag(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch)
    assert collect(tmp_path)["matrix_complete"]
    p = tmp_path / "one-r0/result.json"
    result = json.loads(p.read_text())
    result["status_before_engine_stop"]["owned_jobs"] = 1
    write(p, result)
    report = collect(tmp_path)
    assert not report["matrix_complete"] and not report["aggregates"]
    assert report["passed_cells"] == 2
    assert not report["rows"][1]["checks"]["resources"]


def test_partial_matrix_is_not_silently_averaged(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch)
    (tmp_path / "two-r0/result.json").unlink()
    report = collect(tmp_path)
    assert report["expected_cells"] == 3 and report["passed_cells"] == 2
    assert report["aggregates"] == {}
    assert "FileNotFoundError" in report["rows"][2]["error"]


def test_launch_config_and_source_are_independently_checked(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch)
    path = tmp_path / "one-r0/config.json"
    config = json.loads(path.read_text())
    config["kv_connector_extra_config"]["cachepilot.protection.max_chunks"] = 2
    write(path, config)
    path = tmp_path / "two-r0/run-manifest.json"
    manifest = json.loads(path.read_text()); manifest["source_sha256"] = {"runner": "changed"}
    write(path, manifest)
    report = collect(tmp_path)
    assert report["passed_cells"] == 1 and not report["aggregates"]
