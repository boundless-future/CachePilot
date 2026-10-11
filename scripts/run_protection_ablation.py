"""Run isolated 3C protection-budget performance cells.

This runner deliberately has a different measurement path from the strict
correctness smoke: no KV probes, scheduler snapshots, handoff JSONL, or cache
reset endpoint. Every cell owns a fresh vLLM/LMCache pair and records the
streaming client timings plus final resource state.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import time

import requests

from validate_environment import ROOT, Service, write_json
from generate_trace import generate, validate_trace
from replay_trace import replay
from analyze_replay import summarize


API = "http://127.0.0.1:8000"
CACHE = "http://127.0.0.1:8080"


def text(url):
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.text


def json_url(url):
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def metric(metrics, name, **labels):
    total = 0.0
    for line in metrics.splitlines():
        if not (line.startswith(name + "{") or line.startswith(name + " ")):
            continue
        if all(f'{key}="{value}"' in line for key, value in labels.items()):
            total += float(line.rsplit(" ", 1)[1])
    return total


def cache_drained(state):
    storage = state.get("storage_manager", {})
    fields = (
        ("l1_manager", "read_locked_count"), ("l1_manager", "write_locked_count"),
        ("store_controller", "pending_keys_count"), ("store_controller", "in_flight_task_count"),
        ("prefetch_controller", "in_flight_request_count"),
        ("prefetch_controller", "pending_queue_size"),
        ("prefetch_controller", "lookup_phase_count"),
        ("prefetch_controller", "load_phase_count"),
        ("prefetch_controller", "completed_results_count"),
        ("prefetch_controller", "submission_queue_size"),
    )
    owned = ("owned_jobs", "owned_controller_jobs", "owned_transfers", "owned_deferred_contexts",
             "owned_failed", "owned_read_pins", "owned_store_batches", "active_prefetch_jobs")
    return state.get("is_healthy", False) and all(
        storage.get(section, {}).get(key) == 0 for section, key in fields) and all(
        state.get(key) == 0 for key in owned)


def service_errors(out):
    """An HTTP success and final drain cannot hide a failed RPC handler."""
    errors = []
    for name in ("lmcache.log", "vllm.log"):
        path = out / name
        if not path.exists():
            errors.append(dict(file=name, line=0, text="missing service log"))
            continue
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if re.search(r"\bERROR\b|Traceback \(most recent call last\)", clean):
                errors.append(dict(file=name, line=number, text=clean))
    return errors


def wait_drained(timeout=45):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        try:
            latest = json_url(CACHE + "/status")
            if cache_drained(latest):
                return latest
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return latest or {}


def merge_protection_summary(directory):
    rows = []
    for path in sorted(directory.glob("protection-*.json")):
        rows.append(json.loads(path.read_text()))
    merged = dict(processes=len(rows), events={}, fallbacks={}, prepared_batches=0,
                  committed_batches=0, receipts=0, failed_receipts=0,
                  peak_pinned_blocks=0, peak_inflight_blocks=0, raw=rows,
                  shutdown_closed=len(rows) == 2 and {r["role"] for r in rows} == {"SCHEDULER", "WORKER"})
    for row in rows:
        for key, value in row.get("events", {}).items():
            merged["events"][key] = merged["events"].get(key, 0) + value
        for key, value in row.get("fallbacks", {}).items():
            merged["fallbacks"][key] = merged["fallbacks"].get(key, 0) + value
        for key in ("prepared_batches", "committed_batches", "receipts", "failed_receipts"):
            merged[key] += row.get(key, 0)
        for key in ("peak_pinned_blocks", "peak_inflight_blocks"):
            merged[key] = max(merged[key], row.get(key, 0))
        merged["shutdown_closed"] &= (row.get("shutdown_complete", False) and all(
            row.get(key) == 0 for key in ("pending_handoffs_before_shutdown",
                "prepared_blocks_before_shutdown", "inflight_blocks_before_shutdown")))
    return merged


def source_sha():
    files = []
    for directory in (ROOT / "scripts", ROOT / "configs"):
        for path in sorted(directory.glob("*")):
            if path.is_file() and path.suffix in {".py", ".sh", ".json"}:
                files.append((str(path.relative_to(ROOT)), hashlib.sha256(path.read_bytes()).hexdigest()))
    return dict(files)


def ports_free():
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied")


def staged_trace(tokenizer, context_tokens, pressure_sessions):
    """Protect a moderate working set, then evict it before one final reuse.

    A single saturated 13+ session open loop leaves no safe reserve for a
    bounded pin, while 12 sessions alone never retrieve from CPU. This staged
    trace keeps those mechanisms separate without changing model or capacity.
    """
    base = generate(tokenizer, 12, 5, context_tokens, period_ms=1800,
                    think_ms=100, session_gap_ms=20)
    early = [dict(event) for event in base["events"] if event["turn"] < 4]
    revisit = [dict(event) for event in base["events"] if event["turn"] == 4]
    pressure = generate(tokenizer, pressure_sessions, 1, context_tokens,
                        period_ms=1800, think_ms=100, session_gap_ms=20)
    # Stage boundaries exceed the observed early-phase p99, so protected STORE
    # receipts settle before eviction pressure begins.
    for index, event in enumerate(pressure["events"]):
        event["session_id"] = f"pressure-{index:04d}"
        # A different session label alone does not change the model's KV key.
        event["prompt"] = f"Independent pressure {index}: " + event["prompt"]
        event["prompt_sha256"] = hashlib.sha256(event["prompt"].encode()).hexdigest()
        event["prompt_tokens"] = len(tokenizer.encode(event["prompt"], add_special_tokens=False))
        event["arrival_ms"] = 16000 + index * 20
    for index, event in enumerate(revisit):
        event["arrival_ms"] = 28000 + index * 20
    events = early + pressure["events"] + revisit
    for index, event in enumerate(events):
        event["event_id"] = index
    trace = dict(schema_version=2,
        trace_id=f"staged-s12-p{pressure_sessions}-c{context_tokens}",
        semantics=("Fixed append-only Agent prompts. Early reusable sessions are followed "
                   "by unique pressure prompts and then a delayed fixed reuse."),
        events=events)
    validate_trace(trace)
    return trace


def run_cell(out, mode, repeat, trace, model, kv_bytes):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    # Measurement cells must not inherit any correctness diagnostics.
    for key in list(os.environ):
        if key.startswith("CACHEPILOT_"):
            os.environ.pop(key)
    summary_dir = out / "protection-summary"
    os.environ.update(
        LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080", VLLM_PORT="8000", MODEL_PATH=model,
        CACHEPILOT_OWNED_SERVICE_DIR=str(out / "owned-events"),
        CACHEPILOT_OWNED_EVENT_LOG="0",
        CACHEPILOT_PROTECTION_SUMMARY_PATH=str(summary_dir),
        KV_CACHE_BYTES=str(kv_bytes),
    )
    os.environ.pop("VLLM_DISABLE_REQUEST_ID_RANDOMIZATION", None)
    config = json.loads((ROOT / "configs/protection-zero.json").read_text())
    config["kv_connector_extra_config"]["cachepilot.protection.enabled"] = True
    config["kv_connector_extra_config"]["cachepilot.protection.max_chunks"] = {"zero": 0, "one": 1, "two": 2}[mode]
    write_json(out / "config.json", config)
    write_json(out / "trace.json", trace)
    write_json(out / "run-manifest.json", dict(mode=mode, repeat=repeat,
        kv_cache_bytes=kv_bytes, model=model, trace_id=trace["trace_id"],
        trace_sha256=hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest(),
        source_sha256=source_sha()))
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    command = ["bash", str(ROOT / "scripts/serve.sh"), "baseline", "--enforce-eager",
               "--no-async-scheduling", "--kv-transfer-config", json.dumps(config)]
    engine = Service(command, out / "vllm.log")
    result = dict(mode=mode, repeat=repeat, passed=False, errors=0)
    try:
        cache.start(CACHE + "/status")
        engine.start(API + "/health")
        warmup = requests.post(API + "/v1/completions", json=dict(
            model=model, prompt="warmup", max_tokens=8, temperature=0,
            seed=42, ignore_eos=True), timeout=120)
        warmup.raise_for_status()
        write_json(out / "warmup.json", warmup.json())
        # No reset endpoint: the warmup is intentionally below one LMCache chunk.
        before_vllm, before_cache = text(API + "/metrics"), text(CACHE + "/metrics")
        data = asyncio.run(replay(trace, API, model, "open"))
        after_vllm, after_cache = text(API + "/metrics"), text(CACHE + "/metrics")
        data.update(vllm_before=before_vllm, vllm_after=after_vllm,
            lmcache_before=before_cache, lmcache_after=after_cache,
            policy=mode, repeat=repeat,
            trace_sha256=hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest())
        write_json(out / "requests.json", data)
        result.update(summarize(data), trace_sha256=data["trace_sha256"])
        result["h2d_bytes"] = metric(after_cache, "lmcache_mp_transfer_phase_bytes_total", direction="h2d", phase="staging") - metric(before_cache, "lmcache_mp_transfer_phase_bytes_total", direction="h2d", phase="staging")
        result["d2h_bytes"] = metric(after_cache, "lmcache_mp_transfer_phase_bytes_total", direction="d2h", phase="staging") - metric(before_cache, "lmcache_mp_transfer_phase_bytes_total", direction="d2h", phase="staging")
        state_before_stop = wait_drained()
        result["status_before_engine_stop"] = state_before_stop
        result["runtime_drained"] = cache_drained(state_before_stop)
        engine.stop()
        deadline = time.monotonic() + 30
        state_after = state_before_stop
        while time.monotonic() < deadline:
            try:
                state_after = json_url(CACHE + "/status")
                if not state_after.get("registered_gpu_ids"):
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        result["status_after_engine_stop"] = state_after
        result["protection_summary"] = merge_protection_summary(summary_dir)
        result["resource_drained"] = (result["runtime_drained"] and cache_drained(state_after)
                                      and state_after.get("registered_gpu_ids") == [])
        result["protection_observed"] = (mode == "zero" or
            result["protection_summary"]["committed_batches"] > 0)
        result["retrieval_observed"] = result["h2d_bytes"] > 0
        result["service_errors"] = service_errors(out)
        result["passed"] = (result["errors"] == 0 and result["resource_drained"]
                            and result["protection_observed"] and
                            result["retrieval_observed"] and not result["service_errors"] and
                            result["protection_summary"]["shutdown_closed"])
        write_json(out / "result.json", result)
        if not result["passed"]:
            raise RuntimeError(f"cell failed resource or replay gate: {out}")
        return result
    except BaseException as exc:
        result["error"] = repr(exc)
        raise
    finally:
        engine.stop()
        cache.stop()
        result["service_errors"] = service_errors(out)
        if result["service_errors"]:
            result["passed"] = False
        write_json(out / "result.json", result)
        if result.get("error") is None and not result["passed"]:
            raise RuntimeError(f"cell failed shutdown log gate: {out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=("zero", "one", "two"), default=("zero", "one", "two"))
    # 12 sessions did not trigger external retrieval on the fixed 2 GiB pool.
    parser.add_argument("--sessions", type=int, default=16)
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--context-tokens", type=int, default=1024)
    parser.add_argument("--trace-kind", choices=("open-loop", "staged"), default="staged")
    parser.add_argument("--pressure-sessions", type=int, default=8)
    parser.add_argument("--kv-cache-bytes", type=int, default=2147483648)
    args = parser.parse_args()
    if min(args.repeats, args.sessions, args.turns, args.context_tokens, args.pressure_sessions) < 1:
        parser.error("All workload sizes and repeats must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    ports_free()
    model = str(ROOT / "models/Qwen3-4B")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model)
    trace = (staged_trace(tokenizer, args.context_tokens, args.pressure_sessions)
             if args.trace_kind == "staged" else
             generate(tokenizer, args.sessions, args.turns, args.context_tokens,
                      period_ms=1800, think_ms=100, session_gap_ms=20))
    write_json(args.output / "trace.json", trace)
    results = []
    for repeat in range(args.repeats):
        order = list(args.modes[repeat % len(args.modes):]) + list(args.modes[:repeat % len(args.modes)])
        for mode in order:
            ports_free()
            print(f"START {mode}-r{repeat}", flush=True)
            result = run_cell(args.output / f"{mode}-r{repeat}", mode, repeat, trace, model, args.kv_cache_bytes)
            results.append({k: result.get(k) for k in ("mode", "repeat", "passed", "errors", "ttft_ms", "reuse_ttft_ms", "h2d_bytes", "d2h_bytes", "resource_drained", "protection_summary")})
            write_json(args.output / "summary.json", results)
            print(f"DONE {mode}-r{repeat} errors={result['errors']} reuse_p95={result['reuse_ttft_ms']['p95']}", flush=True)


if __name__ == "__main__":
    main()
