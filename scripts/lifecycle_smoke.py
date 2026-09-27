"""Exercise cancellation while an async LMCache retrieve is delayed.

This is a lifecycle probe.  It records evidence under the requested output
directory and deliberately makes no latency or throughput claim.
"""

import argparse
import json
import os
from pathlib import Path
import time

import requests

from validate_environment import ROOT, Service, counter, get_text, write_json


def prompt(repetitions: int = 180) -> str:
    sentence = (
        "A reusable multi-turn context is loaded from the remote KV tier before "
        "the request continues its deterministic analysis."
    )
    return " ".join(f"Evidence {i}: {sentence}" for i in range(repetitions))


def post(api: str, model: str, text: str, max_tokens: int = 32) -> dict:
    response = requests.post(
        api + "/v1/completions",
        json=dict(model=model, prompt=text, max_tokens=max_tokens,
                  temperature=0, seed=42, ignore_eos=True),
        timeout=240,
    )
    data = response.json()
    response.raise_for_status()
    return data


def cancel_stream(api: str, model: str, text: str, cancel_after: float) -> dict:
    started = time.monotonic()
    response = None
    chunks = 0
    try:
        response = requests.post(
            api + "/v1/completions",
            json=dict(model=model, prompt=text, max_tokens=256, temperature=0,
                      seed=42, ignore_eos=True, stream=True),
            stream=True,
            timeout=(15, 240),
        )
        response.raise_for_status()
        # The lifecycle target is cancellation while the request is still
        # waiting for remote KV.  Do not wait for the first streamed token:
        # the diagnostic connector intentionally delays that token.
        time.sleep(cancel_after)
        return dict(status=response.status_code, chunks_before_close=chunks,
                    elapsed_seconds=time.monotonic() - started)
    except Exception as exc:
        return dict(error=repr(exc), chunks_before_close=chunks,
                    elapsed_seconds=time.monotonic() - started)
    finally:
        if response is not None:
            response.close()


def cache_summary(value: dict) -> dict:
    storage = value["storage_manager"]
    return dict(
        is_healthy=value["is_healthy"],
        active_sessions=value["active_sessions"],
        registered_gpu_ids=value["registered_gpu_ids"],
        l1_read_locked=storage["l1_manager"]["read_locked_count"],
        l1_write_locked=storage["l1_manager"]["write_locked_count"],
        store_pending=storage["store_controller"]["pending_keys_count"],
        store_in_flight=storage["store_controller"]["in_flight_task_count"],
        prefetch_in_flight=storage["prefetch_controller"]["in_flight_request_count"],
        prefetch_pending=storage["prefetch_controller"]["pending_queue_size"],
        prefetch_lookup=storage["prefetch_controller"]["lookup_phase_count"],
        prefetch_load=storage["prefetch_controller"]["load_phase_count"],
        write_ttl_seconds=storage["l1_manager"]["write_ttl_seconds"],
        read_ttl_seconds=storage["l1_manager"]["read_ttl_seconds"],
    )


def metric_summary(value: str) -> dict:
    return dict(
        external_hit_tokens=counter(value, "vllm:external_prefix_cache_hits_total"),
        gpu_hit_tokens=counter(value, "vllm:prefix_cache_hits_total"),
        kv_cache_usage=counter(value, "vllm:kv_cache_usage_perc"),
    )


def lifecycle_summary(directory: Path) -> dict:
    rows = []
    for path in directory.glob("lifecycle-*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            rows.append(json.loads(line))
    aborted = [row for row in rows if row["event"] == "request_finished"
               and row.get("status") == "FINISHED_ABORTED"]
    if len(aborted) != 1:
        raise AssertionError(f"Expected one aborted request, got {len(aborted)}")
    request_id = aborted[0]["request_id"]
    waiting = [row for row in rows if row["event"] == "scheduler_snapshot"
               and any(request.get("request_id") == request_id
                       and request.get("status") == "WAITING_FOR_REMOTE_KVS"
                       for request in row.get("requests", []))]
    delayed = [row for row in rows if row["event"] == "retrieve_delayed"
               and request_id in row.get("request_ids", [])]
    skipped = [row for row in rows if row["event"] == "retrieve_cancelled_before_submit"
               and request_id in row.get("request_ids", [])]
    submitted = [row for row in rows if row["event"] == "retrieve_submitted"
                 and request_id in row.get("request_ids", [])]
    completed = [row for row in rows if row["event"] == "worker_cancel_completion"
                 and request_id in row.get("request_ids", [])]
    acknowledged = [row for row in rows if row["event"] == "scheduler_finished_recving"
                    and request_id in row.get("request_ids", [])]
    after = [row for row in rows if row["event"] == "scheduler_snapshot"
             and acknowledged
             and row["monotonic_ns"] > acknowledged[0]["monotonic_ns"]
             and request_id not in row.get("registered_ids", [])
             and not row.get("tracked_refs")]
    baseline = [row for row in rows if row["event"] == "scheduler_snapshot"
                and row["pid"] == aborted[0]["pid"]
                and row["monotonic_ns"] < waiting[0]["monotonic_ns"]] if waiting else []
    if (not waiting or not delayed or len(skipped) != 1 or submitted
            or len(completed) != 1 or len(acknowledged) != 1 or not after or not baseline):
        raise AssertionError("Cancelled request did not close the expected async lifecycle")
    if after[0]["free_blocks"] != baseline[0]["free_blocks"]:
        raise AssertionError("Cancelled request did not restore the free-block count")
    waited = next(request for request in waiting[0]["requests"]
                  if request.get("request_id") == request_id)
    return dict(request_id=request_id,
                waiting_snapshots=len(waiting),
                waiting_block_count=len(waited["block_ids"]),
                free_blocks_during_wait=waiting[0]["free_blocks"],
                free_blocks_before_request=baseline[0]["free_blocks"],
                free_blocks_after_completion=after[0]["free_blocks"],
                delayed_submit_count=len(delayed),
                cancelled_before_submit_count=len(skipped),
                submitted_count=len(submitted),
                worker_cancel_completion_count=len(completed),
                scheduler_completion_count=len(acknowledged),
                request_registered_after_completion=request_id in after[0]["registered_ids"],
                tracked_refs_after_completion=after[0]["tracked_refs"],
                abort_monotonic_ns=aborted[0]["monotonic_ns"],
                skipped_monotonic_ns=skipped[0]["monotonic_ns"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=8.0)
    parser.add_argument("--cancel-after", type=float, default=1.0)
    parser.add_argument("--wait-seconds", type=float, default=4.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    lifecycle_dir = args.output / "lifecycle"
    lifecycle_dir.mkdir()
    os.environ.update(
        CACHEPILOT_LIFECYCLE_DIR=str(lifecycle_dir),
        CACHEPILOT_RETRIEVE_DELAY=str(args.delay_seconds),
        LMCACHE_PORT="5555",
        LMCACHE_HTTP_PORT="8080",
        VLLM_SERVER_DEV_MODE="1",
    )
    api = "http://127.0.0.1:8000"
    cache_api = "http://127.0.0.1:8080"
    model = str(ROOT / "models/Qwen3-4B")
    text = prompt()
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "lifecycle"], args.output / "vllm.log")
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], args.output / "lmcache.log")
    result = dict(model=model, delay_seconds=args.delay_seconds, prompt_chars=len(text))
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        result["warmup_usage"] = post(api, model, text, max_tokens=8)["usage"]
        time.sleep(args.wait_seconds)
        result["before_cancel_metrics"] = metric_summary(get_text(api + "/metrics"))
        # Clear the first engine's GPU prefix cache while keeping LMCache's
        # CPU copy.  Without this restart the second request is a GPU hit and
        # never enters WAITING_FOR_REMOTE_KVS.
        engine.stop()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                state = requests.get(cache_api + "/status", timeout=5).json()
                if not state.get("registered_gpu_ids"):
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "lifecycle"], args.output / "vllm-retrieve.log")
        engine.start(api + "/health")
        result["cancel"] = cancel_stream(api, model, text, args.cancel_after)
        time.sleep(max(args.wait_seconds, args.delay_seconds + 2))
        result["cache_status_after_cancel"] = cache_summary(
            requests.get(cache_api + "/status", timeout=20).json())
        result["after_cancel_metrics"] = metric_summary(get_text(api + "/metrics"))
        result["follow_up_usage"] = post(api, model, text + "\nFollow-up:", max_tokens=8)["usage"]
        time.sleep(1)
        result["lifecycle"] = lifecycle_summary(lifecycle_dir)
        result["after_follow_up_metrics"] = metric_summary(get_text(api + "/metrics"))
        if result["cancel"].get("status") != 200 or result["cancel"]["chunks_before_close"] != 0:
            raise AssertionError("Client did not disconnect before first token")
        if result["after_follow_up_metrics"]["external_hit_tokens"] <= result["after_cancel_metrics"]["external_hit_tokens"]:
            raise AssertionError("Follow-up did not retrieve the cached prefix")
        result["passed"] = True
    except Exception as exc:
        result.update(passed=False, error=repr(exc))
        raise
    finally:
        engine.stop()
        cache.stop()
        write_json(args.output / "result.json", result)


if __name__ == "__main__":
    main()
