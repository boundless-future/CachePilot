"""Exercise lazy-offload receipt and pin cleanup after a worker STORE failure."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time

import requests

from lifecycle_smoke import cache_summary, prompt
from preemption_smoke import completion
from validate_environment import ROOT, Service, write_json


def summarize(directory):
    rows = sorted((json.loads(line) for path in directory.glob("store-failure-*.jsonl")
                   for line in path.read_text().splitlines()),
                  key=lambda row: row["monotonic_ns"])
    events = lambda name: [row for row in rows if row["event"] == name]
    submitted = events("store_submitted")
    overridden = events("store_result_overridden")
    if len(submitted) != 1 or len(overridden) != 1:
        raise AssertionError("Expected one real STORE future and one overridden result")
    request_id = submitted[0]["request_id"]
    worker = [row for row in events("worker_store_receipt")
              if request_id in row["completed"] or request_id in row["failed"]]
    before = [row for row in events("scheduler_store_receipt_before")
              if row["request_id"] == request_id]
    after = [row for row in events("scheduler_store_receipt_after")
             if row["request_id"] == request_id]
    finished = [row for row in events("request_finished")
                if row["request_id"] == request_id]
    timeline = [submitted[0], overridden[0], *worker, *before, *after]
    if (overridden[0]["request_id"] != request_id or
            overridden[0]["actual_result"] is not True or
            len(worker) != 1 or worker[0]["completed"].get(request_id) != 1 or
            request_id not in worker[0]["failed"] or
            len(before) != 1 or len(after) != 1 or
            not before[0]["failed"] or before[0]["completed"] != 1 or
            not before[0]["in_flight"] or after[0]["in_flight"] or
            after[0]["pending"] or len(finished) != 1 or
            finished[0]["status"] != "FINISHED_LENGTH_CAPPED" or
            any(a["monotonic_ns"] > b["monotonic_ns"]
                for a, b in zip(timeline, timeline[1:]))):
        raise AssertionError("Failed STORE receipt did not close its scheduler batch")
    refs_before, refs_after = before[0]["pinned_refs"], after[0]["pinned_refs"]
    if (not refs_before or refs_before.keys() != refs_after.keys() or
            any(refs_after[bid] != count - 1 for bid, count in refs_before.items())):
        raise AssertionError("STORE receipt did not release exactly one pin per block")
    snapshots = events("scheduler_snapshot")
    baseline = events("block_pool_bound")
    settled = [row for row in snapshots if row["monotonic_ns"] >
               max(finished[0]["monotonic_ns"], after[0]["monotonic_ns"])
               and not row["registered_ids"] and row["deferred_frees"] == 0]
    if (len(baseline) != 1 or not settled or snapshots[-1] != settled[-1] or
            settled[-1]["free_blocks"] != baseline[0]["free_blocks"]):
        raise AssertionError("Failed STORE left allocated GPU blocks")
    return dict(request_id=request_id, final_status=finished[0]["status"],
                released_pins=len(refs_before),
                pending_before_receipt=before[0]["pending"],
                pending_after_receipt=after[0]["pending"],
                in_flight_after_receipt=after[0]["in_flight"],
                free_blocks_before_receipt=before[0]["free_blocks"],
                free_blocks_after_receipt=after[0]["free_blocks"],
                baseline_free_blocks=baseline[0]["free_blocks"],
                final_free_blocks=settled[-1]["free_blocks"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied")
    args.output.mkdir(parents=True, exist_ok=False)
    events = args.output / "events"
    events.mkdir()
    os.environ.update(CACHEPILOT_STORE_FAILURE_DIR=str(events.resolve()),
                      LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080")
    api = "http://127.0.0.1:8000"
    cache_api = "http://127.0.0.1:8080"
    model = str(ROOT / "models/Qwen3-4B")
    text = prompt()
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], args.output / "lmcache.log")
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "store-failure"],
                     args.output / "vllm.log")
    result = dict(model=model, prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                  failure_mode="false result after real STORE")
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        target = completion(api, model, text, 256)
        write_json(args.output / "target.json", target)
        result["target_usage"] = target["usage"]
        if target["usage"]["completion_tokens"] != 256:
            raise AssertionError("Target generation did not complete")
        time.sleep(1)
        follow_up = completion(api, model, text + "\nFollow-up:", 32)
        result["follow_up_usage"] = follow_up["usage"]
        time.sleep(2)
        result["lifecycle"] = summarize(events)
        result["cache_status"] = cache_summary(requests.get(cache_api + "/status", timeout=20).json())
        status = result["cache_status"]
        if (not status["is_healthy"] or status["store_pending"] or
                status["store_in_flight"] or status["prefetch_in_flight"] or
                status["l1_read_locked"] or status["l1_write_locked"]):
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
