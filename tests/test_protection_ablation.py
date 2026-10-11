import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_protection_ablation import staged_trace, cache_drained, metric, merge_protection_summary, service_errors
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
