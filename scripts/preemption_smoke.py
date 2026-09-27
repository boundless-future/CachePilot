"""Trigger vLLM's explicit running-request preemption through its dev API."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import socket

import requests

from validate_environment import ROOT, Service, write_json
from lifecycle_smoke import cache_summary, prompt


def completion(api, model, text, max_tokens):
    response = requests.post(
        api + "/v1/completions",
        json=dict(model=model, prompt=text, max_tokens=max_tokens,
                  temperature=0, seed=42, ignore_eos=True),
        timeout=240,
    )
    response.raise_for_status()
    data = response.json()
    return dict(text=data["choices"][0]["text"], usage=data["usage"])


def preempted_completion(api, model, text, max_tokens):
    response = requests.post(
        api + "/v1/completions",
        json=dict(model=model, prompt=text, max_tokens=max_tokens,
                  temperature=0, seed=42, ignore_eos=True, stream=True,
                  stream_options=dict(include_usage=True)),
        stream=True,
        timeout=(15, 240),
    )
    response.raise_for_status()
    chunks = []
    reset = None
    usage = None
    finished = None
    done = False
    try:
        for line in response.iter_lines(chunk_size=1):
            if line == b"data: [DONE]":
                done = True
                break
            if not line:
                continue
            if not line.startswith(b"data: "):
                continue
            data = json.loads(line[6:])
            if data.get("usage"):
                usage = data["usage"]
            for choice in data.get("choices", []):
                chunks.append(choice.get("text", ""))
                finished = choice.get("finish_reason") or finished
            if reset is None and any(chunks):
                reply = requests.post(
                    api + "/reset_prefix_cache",
                    params=dict(reset_running_requests="true", reset_external="false"),
                    timeout=120,
                )
                try:
                    body = reply.json()
                except ValueError:
                    body = reply.text
                reset = dict(http_status=reply.status_code, body=body)
    finally:
        response.close()
    if reset is None:
        raise AssertionError("Request finished without a preemption trigger")
    if not done:
        raise AssertionError("Preempted stream ended without [DONE]")
    return dict(text="".join(chunks), chunks=len(chunks), usage=usage,
                finish_reason=finished, reset=reset)


def preemption_summary(directory):
    rows = sorted([json.loads(line)
            for path in directory.glob("preemption-*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()],
            key=lambda row: row["monotonic_ns"])
    before = [row for row in rows if row["event"] == "preempt_before"]
    after = [row for row in rows if row["event"] == "preempt_after"]
    if len(before) != 1 or len(after) != 1:
        raise AssertionError("Expected exactly one explicit preemption")
    request_id = before[0]["request_id"]
    finished = [row for row in rows if row["event"] == "request_finished"
                and row["request_id"] == request_id]
    freed = [row for row in rows if row["event"] == "blocks_freed"
             and row["request_id"] == request_id]
    receipts = [row for row in rows if row["event"] == "store_receipt"
                and request_id in row.get("completed", [])]
    failed = [row for row in rows if row["event"] == "store_receipt"
              and request_id in row.get("failed", [])]
    baseline = [row for row in rows if row["event"] == "scheduler_snapshot"
                and row["monotonic_ns"] < before[0]["monotonic_ns"]
                and not row["registered_ids"]]
    settled = [row for row in rows if row["event"] == "scheduler_snapshot"
               and finished and row["monotonic_ns"] > finished[0]["monotonic_ns"]
               and not row["registered_ids"] and not row["tracked_refs"]
               and not row["deferred_frees"]]
    snapshots = [row for row in rows if row["event"] == "scheduler_snapshot"]
    if (before[0]["status"] != "RUNNING" or not before[0]["block_ids"]
            or before[0]["num_preemptions"] != 0
            or after[0]["request_id"] != request_id
            or after[0]["monotonic_ns"] <= before[0]["monotonic_ns"]
            or after[0]["status"] != "PREEMPTED"
            or after[0]["num_preemptions"] != 1
            or after[0]["computed_tokens"] != 0 or not after[0]["registered"]
            or len(finished) != 1 or finished[0]["num_preemptions"] != 1
            or finished[0]["monotonic_ns"] <= after[0]["monotonic_ns"]
            or finished[0]["status"] != "FINISHED_LENGTH_CAPPED"
            or not freed or freed[-1]["registered"] or freed[-1]["allocation_present"]
            or (finished and freed and freed[-1]["monotonic_ns"] <= finished[0]["monotonic_ns"])
            or failed or not baseline or not settled
            or snapshots[-1] != settled[-1]
            or settled[-1]["free_blocks"] != baseline[0]["free_blocks"]):
        raise AssertionError("Preempted request did not close its lifecycle")
    return dict(
        request_id=request_id,
        allocated_blocks_before_preempt=len(before[0]["block_ids"]),
        free_blocks_before_preempt=before[0]["free_blocks"],
        free_blocks_after_preempt=after[0]["free_blocks"],
        final_status=finished[0]["status"],
        final_num_preemptions=finished[0]["num_preemptions"],
        baseline_free_blocks=baseline[0]["free_blocks"],
        final_free_blocks=settled[-1]["free_blocks"],
        tracked_refs_after_finish=settled[-1]["tracked_refs"],
        deferred_frees_after_finish=settled[-1]["deferred_frees"],
        request_registered_after_finish=freed[-1]["registered"],
        allocation_present_after_finish=freed[-1]["allocation_present"],
        completed_store_receipts=len(receipts),
        failed_store_receipts=len(failed),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--no-async-scheduling", action="store_true",
                        help="Control run avoiding reset API's deferred-block-free window")
    args = parser.parse_args()
    if args.max_tokens < 32:
        parser.error("--max-tokens must be at least 32 to leave a preemption window")
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied")
    args.output.mkdir(parents=True, exist_ok=False)
    event_dir = args.output / "events"
    event_dir.mkdir()
    os.environ.update(
        CACHEPILOT_PREEMPTION_DIR=str(event_dir),
        LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080",
        VLLM_SERVER_DEV_MODE="1",
    )
    api = "http://127.0.0.1:8000"
    cache_api = "http://127.0.0.1:8080"
    model = str(ROOT / "models/Qwen3-4B")
    text = prompt()
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], args.output / "lmcache.log")
    command = ["bash", str(ROOT / "scripts/serve.sh"), "preemption"]
    if args.no_async_scheduling:
        command.append("--no-async-scheduling")
    engine = Service(command, args.output / "vllm.log")
    result = dict(model=model, max_tokens=args.max_tokens, prompt_chars=len(text),
                  prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                  no_async_scheduling=args.no_async_scheduling)
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        reference = completion(api, model, text, args.max_tokens)
        write_json(args.output / "reference.json", reference)
        result["reference_usage"] = reference["usage"]
        target = preempted_completion(api, model, text, args.max_tokens)
        write_json(args.output / "target.json", target)
        result["target"] = {key: value for key, value in target.items() if key != "text"}
        result["outputs_equal"] = reference["text"] == target["text"]
        time.sleep(3)
        result["follow_up_usage"] = completion(api, model, text + "\nFollow-up:", 8)["usage"]
        time.sleep(1)
        result["lifecycle"] = preemption_summary(event_dir)
        result["cache_status"] = cache_summary(requests.get(cache_api + "/status", timeout=20).json())
        if not result["outputs_equal"]:
            raise AssertionError("Preemption changed greedy output")
        if target["finish_reason"] != "length" or target["usage"]["completion_tokens"] != args.max_tokens:
            raise AssertionError("Preempted request did not complete")
        if (not result["cache_status"]["is_healthy"]
                or result["cache_status"]["store_pending"]
                or result["cache_status"]["store_in_flight"]
                or result["cache_status"]["prefetch_in_flight"]
                or result["cache_status"]["prefetch_pending"]
                or result["cache_status"]["prefetch_lookup"]
                or result["cache_status"]["prefetch_load"]):
            raise AssertionError("LMCache did not settle after preemption")
        if (target["reset"]["http_status"] != 200
                or not isinstance(target["reset"]["body"], dict)
                or target["reset"]["body"].get("success") is not True):
            raise AssertionError("Reset API failed despite the recorded preemption")
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
