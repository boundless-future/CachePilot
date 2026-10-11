import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_protection_ablation import (staged_trace, cache_drained, metric, merge_protection_summary,
                                     service_errors, service_error_report)
import json
import pytest


class Tokenizer:
    def encode(self, text, add_special_tokens=False):
        return list(range(len(text.split())))

    def decode(self, ids):
        return " ".join("x" for _ in ids)


def test_staged_trace_reuses_after_separate_pressure_phase():
    trace = staged_trace(Tokenizer(), 64, 8, "validation")
    events = trace["events"]
    early = [event for event in events if event["session_id"].startswith("s") and event["turn"] < 4]
    pressure = [event for event in events if event["session_id"].startswith("pressure-")]
    revisit = [event for event in events if event["session_id"].startswith("s") and event["turn"] == 4]
    assert len(early) == 48 and len(pressure) == 8 and len(revisit) == 12
    assert max(event["arrival_ms"] for event in early) < min(event["arrival_ms"] for event in pressure)
    assert max(event["arrival_ms"] for event in pressure) < min(event["arrival_ms"] for event in revisit)
    assert not {e["prompt_sha256"] for e in early} & {e["prompt_sha256"] for e in pressure}
    assert trace["trace_id"].startswith("staged-validation-")
    assert all(e["prompt"].startswith("Workload validation. ") for e in events)


def healthy():
    return dict(is_healthy=True, storage_manager=dict(
        l1_manager=dict(read_locked_count=0, write_locked_count=0),
        store_controller=dict(pending_keys_count=0, in_flight_task_count=0),
        prefetch_controller=dict(in_flight_request_count=0, pending_queue_size=0,
            lookup_phase_count=0, load_phase_count=0, completed_results_count=0,
            submission_queue_size=0)), **{k: 0 for k in ("owned_jobs", "owned_controller_jobs",
            "owned_transfers", "owned_deferred_contexts", "owned_failed", "owned_read_pins",
            "owned_store_batches", "active_prefetch_jobs")})


@pytest.mark.parametrize("key", ["owned_jobs", "owned_failed", "owned_read_pins", "owned_transfers"])
def test_no_active_pins_does_not_mean_no_owners(key):
    state = healthy()
    assert cache_drained(state)
    state[key] = 1
    assert not cache_drained(state)
    del state[key]
    assert not cache_drained(state)


def test_completed_prefetch_and_missing_status_fail_closed():
    state = healthy()
    state["storage_manager"]["prefetch_controller"]["completed_results_count"] = 1
    assert not cache_drained(state)
    assert not cache_drained({"is_healthy": True})


def test_dma_counter_excludes_other_direction_phase_and_created():
    raw = '\n'.join(['bytes{direction="h2d",phase="staging"} 32',
        'bytes{direction="h2d",phase="kernel"} 32',
        'bytes{direction="d2h",phase="staging"} 64',
        'bytes_created{direction="h2d",phase="staging"} 200'])
    assert metric(raw, "bytes", direction="h2d", phase="staging") == 32


def test_missing_process_summary_is_not_success(tmp_path):
    assert not merge_protection_summary(tmp_path)["shutdown_closed"]
    for role in ("SCHEDULER", "WORKER"):
        (tmp_path / f"protection-1-{role}.json").write_text(json.dumps(dict(role=role,
            shutdown_complete=True, pending_handoffs_before_shutdown=0,
            prepared_blocks_before_shutdown=0, inflight_blocks_before_shutdown=0)))
    assert merge_protection_summary(tmp_path)["shutdown_closed"]


def test_rpc_error_log_fails_even_when_http_succeeds(tmp_path):
    (tmp_path / "vllm.log").write_text('POST /v1/completions 200 OK\n')
    (tmp_path / "lmcache.log").write_text('\x1b[31m LMCache ERROR:\x1b[0m Error in blocking handler\n')
    errors = service_errors(tmp_path)
    assert len(errors) == 1 and errors[0]["file"] == "lmcache.log"
    (tmp_path / "lmcache.log").write_text('INFO shutdown complete\n')
    assert not service_errors(tmp_path)



@pytest.fixture
def shutdown_log(tmp_path):
    before = [
        "INFO [shutdown] API server: shutdown triggered",
        "INFO [shutdown] Process manager: send sigterm to process EngineCore",
        "INFO [shutdown] EngineCore: trigger received signal=SIGTERM",
        "INFO [shutdown] EngineCore: request processing complete; starting resource teardown",
        "INFO [shutdown] MPClient: engine manager stopped",
    ]
    payload = [
        "AsyncLLM output_handler failed.",
        "Traceback (most recent call last):",
        '  File "/site/vllm/v1/engine/async_llm.py", line 789, in output_handler',
        "    outputs = await engine_core.get_output_async()",
        "              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^",
        '  File "/site/vllm/v1/engine/core_client.py", line 1175, in get_output_async',
        "    raise self._format_exception(outputs) from None",
        "vllm.v1.engine.exceptions.EngineDeadError: EngineCore encountered an issue. See stack trace (above) for the root cause.",
    ]
    error = ["(APIServer pid=123) ERROR 10-11 08:09:35 [async_llm.py:843] " + p for p in payload]
    end = ["(APIServer pid=123) INFO:     Application shutdown complete."]
    (tmp_path / "lmcache.log").write_text("INFO complete\n")
    (tmp_path / "vllm.log").write_text("\n".join(before + error + end))
    context = dict(pid=123, log_lines_before_stop=0, returncode=0,
                   requests_complete=True, process_alive=True, runtime_drained=True)
    return tmp_path, before, error, end, context


def test_only_known_orderly_shutdown_output_handler_race_is_separated(shutdown_log):
    path, _, _, _, context = shutdown_log
    report = service_error_report(path, context)
    assert not report["fatal_errors"]
    assert len(report["shutdown_teardown_errors"]) == 8
    # Old failed artifacts have no runner boundary: never reinterpret them.
    assert len(service_errors(path)) == 8


@pytest.mark.parametrize("change", [
    {"requests_complete": False}, {"runtime_drained": False}, {"process_alive": False},
    {"returncode": -9}, {"returncode": None}, {"pid": 999},
    {"log_lines_before_stop": 6}, {"log_lines_before_stop": -1},
])
def test_missing_or_invalid_runner_evidence_fails_closed(shutdown_log, change):
    path, _, _, _, context = shutdown_log
    context.update(change)
    assert len(service_errors(path, context)) == 8


@pytest.mark.parametrize("mutation", ["reordered", "missing_marker", "runtime", "incomplete",
                                       "other_exception", "missing_exit", "interleaved"])
def test_unknown_or_runtime_errors_are_fatal(shutdown_log, mutation):
    path, before, error, end, context = shutdown_log
    if mutation == "reordered":
        before.reverse()
    elif mutation == "missing_marker":
        before.pop(2)
    elif mutation == "runtime":
        before, error = error, before
    elif mutation == "incomplete":
        error.pop(4)
    elif mutation == "other_exception":
        error[-1] = error[-1].replace("EngineDeadError", "RuntimeError")
    elif mutation == "missing_exit":
        end = []
    elif mutation == "interleaved":
        error.insert(3, "ERROR CUDA illegal memory access")
    (path / "vllm.log").write_text("\n".join(before + error + end))
    report = service_error_report(path, context)
    assert report["fatal_errors"]
    assert not report["shutdown_teardown_errors"]


@pytest.mark.parametrize("file", ["vllm.log", "lmcache.log"])
def test_adjacent_unrelated_error_is_never_swallowed(shutdown_log, file):
    path, before, error, end, context = shutdown_log
    if file == "vllm.log":
        (path / file).write_text("\n".join(before + error + ["ERROR CUDA illegal memory access"] + end))
    else:
        (path / file).write_text("ERROR Error in blocking handler")
    report = service_error_report(path, context)
    assert len(report["fatal_errors"]) == 1
    assert len(report["shutdown_teardown_errors"]) == 8
