"""Validate recomputation after one completed LMCache load reports failure."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time

import requests

from lifecycle_smoke import cache_summary, metric_summary, prompt
from preemption_smoke import completion
from validate_environment import ROOT, Service, get_text, write_json


def summarize(directory, mode="receipt-only"):
    if mode not in ("receipt-only", "server-reject"):
        raise ValueError(f"Unknown retrieve failure mode: {mode}")
    rows = sorted((json.loads(line) for path in directory.glob("retrieve-failure-*.jsonl")
                   for line in path.read_text().splitlines()),
                  key=lambda row: row["monotonic_ns"])
    events = lambda name: [row for row in rows if row["event"] == name]
    submitted = events("retrieve_submitted")
    outcome = events("retrieve_result_overridden" if mode == "receipt-only"
                     else "server_retrieve_result")
    invalidated = events("retrieve_payload_invalidated")
    errors = events("worker_load_errors")
    worker_finished = events("worker_get_finished")
    invalid = events("scheduler_invalid_blocks")
    handled = events("scheduler_invalid_handled")
    finished = events("scheduler_finished_recving")
    if len(submitted) != 1 or len(outcome) != 1:
        raise AssertionError("Expected one submitted retrieve and observed result")
    request_id = submitted[0]["request_id"]
    block_ids = set(submitted[0]["block_ids"])
    if (not block_ids or outcome[0]["request_id"] != request_id
            or outcome[0]["actual_result"] is not (mode == "receipt-only")
            or (mode == "server-reject" and
                (len(invalidated) != 1 or invalidated[0]["request_id"] != request_id
                 or not invalidated[0]["original_block_counts"]
                 or any(n <= 0 for n in invalidated[0]["original_block_counts"])
                 or any(invalidated[0]["submitted_block_counts"])
                 or invalidated[0]["monotonic_ns"] > submitted[0]["monotonic_ns"]))
            or (mode == "receipt-only" and invalidated)
            or len(errors) != 1 or set(errors[0]["block_ids"]) != block_ids
            or len(invalid) != 1 or set(invalid[0]["block_ids"]) != block_ids
            or not invalid[0]["recompute"] or len(handled) != 1
            or request_id not in handled[0]["failed_recving"]
            or len([row for row in worker_finished
                    if request_id in row["receiving"]]) != 1
            or len([row for row in finished if request_id in row["request_ids"]]) != 1):
        raise AssertionError("Retrieve failure did not reach the scheduler recompute path")
    timeline = [submitted[0], outcome[0], errors[0], invalid[0], handled[0],
                next(row for row in finished if request_id in row["request_ids"])]
    if any(a["monotonic_ns"] > b["monotonic_ns"] for a, b in zip(timeline, timeline[1:])):
        raise AssertionError("Failure evidence is out of order")
    snapshots = events("scheduler_snapshot")
    waiting = [row for row in snapshots if row["monotonic_ns"] < invalid[0]["monotonic_ns"]
               and any(item["request_id"] == request_id and
                       item["status"] == "WAITING_FOR_REMOTE_KVS"
                       for item in row["requests"])]
    reset = [row for row in snapshots if row["monotonic_ns"] > handled[0]["monotonic_ns"]
             and any(item["request_id"] == request_id and
                     item["computed_tokens"] == 0 for item in row["requests"])]
    recompute = [row for row in snapshots if reset and
                 row["monotonic_ns"] > reset[0]["monotonic_ns"] and
                 any(item["request_id"] == request_id and
                     item["status"] == "RUNNING" and item["computed_tokens"] > 0
                     for item in row["requests"])]
    terminal = [row for row in events("request_finished") if row["request_id"] == request_id]
    baseline = [row for row in snapshots if row["monotonic_ns"] < submitted[0]["monotonic_ns"]
                and not any(item["block_ids"] for item in row["requests"])
                and not row["tracked_refs"]]
    settled = [row for row in snapshots if terminal and
               row["monotonic_ns"] > terminal[0]["monotonic_ns"] and
               not row["registered_ids"] and not row["tracked_refs"] and
               row["deferred_frees"] == 0]
    if (not waiting or not reset or not recompute or len(terminal) != 1
            or terminal[0]["status"] != "FINISHED_LENGTH_CAPPED"
            or not baseline or not settled
            or settled[-1]["free_blocks"] != baseline[-1]["free_blocks"]):
        raise AssertionError("Recomputed request did not finish and release its blocks")
    return dict(request_id=request_id, failed_blocks=len(block_ids),
                waiting_snapshots=len(waiting), recompute_snapshots=len(recompute),
                final_status=terminal[0]["status"],
                baseline_free_blocks=baseline[-1]["free_blocks"],
                final_free_blocks=settled[-1]["free_blocks"],
                tracked_refs_after_finish=settled[-1]["tracked_refs"],
                deferred_frees_after_finish=settled[-1]["deferred_frees"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("receipt-only", "server-reject"),
                        default="receipt-only")
    parser.add_argument("--owned-guard", action="store_true")
    args = parser.parse_args()
    if args.owned_guard and args.mode != "server-reject":
        parser.error("--owned-guard requires --mode server-reject")
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied")
    args.output.mkdir(parents=True, exist_ok=False)
    events = args.output / "events"
    events.mkdir()
    os.environ.update(CACHEPILOT_RETRIEVE_FAILURE_DIR=str(events.resolve()),
                      LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080")
    if args.owned_guard:
        os.environ["CACHEPILOT_OWNED_MP_DIR"] = str(events.resolve())
    api = "http://127.0.0.1:8000"
    cache_api = "http://127.0.0.1:8080"
    model = str(ROOT / "models/Qwen3-4B")
    text = prompt()
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], args.output / "lmcache.log")
    serve_mode = ("retrieve-failure" if args.mode == "receipt-only"
                  else "owned-server-reject" if args.owned_guard
                  else "server-rejected-retrieve")
    command = ["bash", str(ROOT / "scripts/serve.sh"), serve_mode]
    engine = Service(command, args.output / "vllm-warmup.log")
    result = dict(model=model, prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                  prompt_chars=len(text), failure_mode=(
                      "false result after real retrieve" if args.mode == "receipt-only"
                      else "server rejects underflowed retrieve"))
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        reference = completion(api, model, text, 32)
        write_json(args.output / "reference.json", reference)
        result["reference_usage"] = reference["usage"]
        time.sleep(3)
        engine.stop()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = requests.get(cache_api + "/status", timeout=5).json()
            if not state.get("registered_gpu_ids"):
                break
            time.sleep(0.5)
        else:
            raise AssertionError("Warmup GPU registration did not clear")
        engine = Service(command, args.output / "vllm-retrieve.log")
        engine.start(api + "/health")
        result["cache_status_before_target"] = cache_summary(
            requests.get(cache_api + "/status", timeout=20).json())
        if (args.mode == "server-reject" and
                result["cache_status_before_target"]["l1_read_locked"] != 0):
            raise AssertionError("Warmup left read locks before the rejection probe")
        before = metric_summary(get_text(api + "/metrics"))
        target = completion(api, model, text, 32)
        write_json(args.output / "target.json", target)
        result["target_usage"] = target["usage"]
        result["outputs_equal"] = target["text"] == reference["text"]
        result["target_external_hit_delta"] = (
            metric_summary(get_text(api + "/metrics"))["external_hit_tokens"]
            - before["external_hit_tokens"])
        time.sleep(2)
        result["lifecycle"] = summarize(events, mode=args.mode)
        if args.mode == "server-reject":
            if args.owned_guard:
                guarded = [json.loads(line) for path in events.glob("owned-mp-*.jsonl")
                           for line in path.read_text().splitlines()]
                rejected = [row for row in guarded
                            if row["event"] == "retrieve_rejected_blocks" and
                            row["request_id"] == result["lifecycle"]["request_id"]]
                if len(rejected) != 1:
                    raise AssertionError("Guard did not reject one short RETRIEVE")
                result["guard_rejection"] = rejected[0]
            else:
                rejection = ("RETRIEVE block ID underflow for request_id=" +
                             result["lifecycle"]["request_id"])
                if rejection not in (args.output / "lmcache.log").read_text(errors="replace"):
                    raise AssertionError("MP server did not log the expected RETRIEVE rejection")
            result["server_rejection_logged"] = True
        result["cache_status_after_target"] = cache_summary(
            requests.get(cache_api + "/status", timeout=20).json())
        follow_up = completion(api, model, text + "\nFollow-up:", 8)
        result["follow_up_usage"] = follow_up["usage"]
        result["cache_status"] = cache_summary(requests.get(cache_api + "/status", timeout=20).json())
        if not result["outputs_equal"] or target["usage"]["completion_tokens"] != 32:
            raise AssertionError("Recomputed output differs from greedy reference")
        if result["target_external_hit_delta"] <= 0:
            raise AssertionError("Target did not claim an external prefix hit")
        status = result["cache_status"]
        if (not status["is_healthy"] or status["store_pending"] or
                status["store_in_flight"] or status["prefetch_in_flight"] or
                status["prefetch_pending"] or status["prefetch_lookup"] or
                status["prefetch_load"] or status["l1_read_locked"] or
                status["l1_write_locked"]):
            raise AssertionError("LMCache resources did not settle")
        result["passed"] = True
    except Exception as exc:
        result.update(passed=False, error=repr(exc))
        raise
    finally:
        engine.stop()
        if cache.process and cache.process.poll() is None:
            try:
                result["cache_status_after_engine_stop"] = cache_summary(
                    requests.get(cache_api + "/status", timeout=20).json())
            except requests.RequestException as exc:
                result["cache_status_after_engine_stop_error"] = repr(exc)
        cache.stop()
        write_json(args.output / "result.json", result)


if __name__ == "__main__":
    main()
