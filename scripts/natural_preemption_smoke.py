"""KV-capacity pressure triggers scheduler preemption; no reset API injection.

Uses the existing read-only PreemptionConnector. This is a lifecycle workload
with diagnostic overhead, not a benchmark or an output-equivalence proof.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import time

import requests

from l2_prefetch_cancel_smoke import events, drained
from remote_lookup_cancel_smoke import cache_state, wait_for
from validate_environment import ROOT, Service, write_json


def audit(rows, expected_requests):
    rows = sorted(rows, key=lambda r: r["monotonic_ns"])
    named = lambda name: [r for r in rows if r["event"] == name]
    before, after = named("preempt_before"), named("preempt_after")
    finished, snapshots = named("request_finished"), named("scheduler_snapshot")
    bound = named("block_pool_bound")
    assert before and len(before) == len(after), "No complete natural preemption evidence"
    for b, a in zip(before, after):
        assert b["request_id"] == a["request_id"] and b["status"] == "RUNNING"
        assert a["num_preemptions"] == b["num_preemptions"] + 1
        assert a["status"] == "PREEMPTED" and a["computed_tokens"] == 0
    assert len(finished) == expected_requests
    assert len({r["request_id"] for r in finished}) == expected_requests
    assert all(r["status"] == "FINISHED_LENGTH_CAPPED" for r in finished)
    assert sum(r["num_preemptions"] for r in finished) == len(before)
    final = snapshots[-1]
    assert len(bound) == 1 and final["free_blocks"] == bound[0]["free_blocks"]
    assert not final["registered_ids"] and not final["deferred_frees"] and not final["tracked_refs"]
    assert not any(r["failed"] for r in named("store_receipt"))
    return dict(preemptions=len(before), preempted_requests=len({r["request_id"] for r in before}),
        preemptions_with_store_inflight=sum(r["store_inflight"] for r in before),
        final_free_blocks=final["free_blocks"], initial_free_blocks=bound[0]["free_blocks"],
        all_requests_finished=True)


def audit_receipts(rows):
    pending = {}
    released = orphaned = 0
    for row in sorted(rows, key=lambda r: r["monotonic_ns"]):
        if row["event"] == "scheduler_receipt_before":
            request_id = row["request_id"]
            assert request_id not in pending
            pending[request_id] = row
        elif row["event"] == "scheduler_receipt_after":
            before = pending.pop(row["request_id"])
            refs = before["pinned_refs"]
            assert before["completed"] == 1 and not before["failed"]
            assert refs and refs.keys() == row["pinned_refs"].keys()
            assert all(row["pinned_refs"][bid] == count - 1 for bid, count in refs.items())
            assert not row["in_flight"]
            released += len(refs)
            orphaned += int(before["orphaned"])
    assert not pending
    return dict(released_pin_references=released, orphaned_receipts=orphaned)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=8)
    parser.add_argument("--decode-tokens", type=int, default=2048)
    parser.add_argument("--prompt-tokens", type=int, default=1536)
    parser.add_argument("--kv-bytes", type=int, default=1073741824)
    parser.add_argument("--receipt-delay", type=float, default=0)
    parser.add_argument("--observe-after-stop", type=float, default=0)
    parser.add_argument("--kv-probe", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.receipt_delay <= 30:
        parser.error("Receipt delay must be in [0,30]")
    if args.kv_probe and args.receipt_delay <= 0:
        parser.error("KV probe uses a positive controlled receipt delay")
    if not 0 <= args.observe_after_stop <= 900:
        parser.error("Post-stop observation must be in [0,900]")
    if not 2 <= args.requests <= 16 or not 32 <= args.decode_tokens <= 3072 or not 32 <= args.prompt_tokens <= 2048:
        parser.error("Experiment counts outside bounded range")
    if args.decode_tokens + args.prompt_tokens > 4096:
        parser.error("Prompt + decode must fit 4096 tokens")
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} occupied")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    directory = out / "events"
    directory.mkdir()
    os.environ.update(CACHEPILOT_PREEMPTION_DIR=str(directory),
        LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080", KV_CACHE_BYTES=str(args.kv_bytes),
        LMCACHE_TRACK_USAGE="false")
    os.environ["CACHEPILOT_STORE_RECEIPT_DELAY"] = str(args.receipt_delay)
    os.environ.pop("VLLM_SERVER_DEV_MODE", None)
    from transformers import AutoTokenizer
    model = str(ROOT / "models/Qwen3-4B")
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
    prompts = [tokenizer.encode(f"Workload {i}: " + ("Continue listing facts about caching and inference. " * 300),
        add_special_tokens=False)[:args.prompt_tokens] for i in range(args.requests)]
    assert all(len(p) == args.prompt_tokens for p in prompts)
    write_json(out / "prompts.json", prompts)
    api, cache_api = "http://127.0.0.1:8000", "http://127.0.0.1:8080"
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    mode = "late-store-receipt" if args.receipt_delay else "preemption"
    if args.kv_probe:
        mode = "preemption-kv-probe"
        os.environ["CACHEPILOT_PROBE_DIR"] = str(out / "probe")
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), mode, "--enforce-eager",
                      "--max-model-len", "4096", "--max-num-batched-tokens", "2048"], out / "vllm.log")
    result = dict(passed=False, injection="receipt observation delay; capacity pressure" if args.receipt_delay else "none; capacity pressure", requests=args.requests,
        prompt_tokens=args.prompt_tokens, decode_tokens=args.decode_tokens, kv_bytes=args.kv_bytes,
        connector="LateStoreReceiptConnector" if args.receipt_delay else "PreemptionConnector",
        receipt_delay_seconds=args.receipt_delay, eager=True, output_equivalence_test=False,
        observe_after_stop_seconds=args.observe_after_stop,
        prompt_sha256=hashlib.sha256(json.dumps(prompts).encode()).hexdigest())
    result["kv_probe_enabled"] = args.kv_probe
    if args.kv_probe:
        result["connector"] = "PreemptionKVProbeConnector"

    def send(index):
        started = time.monotonic()
        response = requests.post(api + "/v1/completions", json=dict(model=model, prompt=prompts[index],
            max_tokens=args.decode_tokens, temperature=0, seed=42, ignore_eos=True), timeout=600)
        data = response.json()
        write_json(out / f"request-{index}.json", data)
        response.raise_for_status()
        assert data["usage"]["completion_tokens"] == args.decode_tokens
        assert data["choices"][0]["finish_reason"] == "length"
        return dict(index=index, usage=data["usage"], elapsed_seconds=time.monotonic()-started)

    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        print("Submitting natural capacity-pressure workload", flush=True)
        with ThreadPoolExecutor(max_workers=args.requests) as pool:
            result["responses"] = list(pool.map(send, range(args.requests)))
        wait_for(lambda: drained(cache_state(cache_api)), 30)
        # L2/L1 queues can be empty while scheduler receipt ownership is held.
        def scheduler_settled():
            snapshots = [r for r in events(directory) if r["event"] == "scheduler_snapshot"]
            return snapshots and not snapshots[-1]["registered_ids"] and not snapshots[-1]["deferred_frees"] and not snapshots[-1]["tracked_refs"]
        wait_for(scheduler_settled, max(30, args.receipt_delay + 10))
        time.sleep(1)
        result["lifecycle"] = audit(events(directory), args.requests)
        result["receipt_audit"] = audit_receipts(events(directory))
        if args.receipt_delay:
            rows = events(directory)
            held = [r for r in rows if r["event"] == "store_receipt_held"]
            delivered = [r for r in rows if r["event"] == "held_store_result_delivered"]
            before = [r for r in rows if r["event"] == "scheduler_receipt_before" and r["orphaned"]]
            assert held and len(held) == len(delivered)
            assert all(r["actual_result"] is True for r in delivered)
            result["held_receipts"] = dict(submitted=len(held), delivered=len(delivered),
                orphaned_scheduler_receipts=len(before),
                actual_ready_before_release=sum(r["event"] == "actual_store_ready" and
                    r["remaining_hold_seconds"] > 0 for r in rows))
        result["cache_state"] = cache_state(cache_api)
        write_json(out / "after-status.json", requests.get(cache_api + "/status", timeout=10).json())
        if args.kv_probe:
            from analyze_preemption_kv import audit_probe_sources
            result["probe_followups"] = []
            for item in sorted(result["responses"], key=lambda r:r["elapsed_seconds"]):
                index = item["index"]
                response = requests.post(api + "/v1/completions", json=dict(
                    model=model, prompt=prompts[index], max_tokens=32,
                    temperature=0, seed=42, ignore_eos=True), timeout=120)
                response.raise_for_status()
                data = response.json()
                write_json(out / f"followup-{index}.json", data)
                assert data["usage"]["completion_tokens"] == 32
                result["probe_followups"].append(dict(index=index, usage=data["usage"]))
            wait_for(scheduler_settled, max(30, args.receipt_delay + 10))
            wait_for(lambda: drained(cache_state(cache_api)), 30)
            lifecycle_rows = events(directory)
            snapshots = sorted((r for r in lifecycle_rows if r["event"] == "scheduler_snapshot"),
                               key=lambda r:r["monotonic_ns"])
            assert snapshots[-1]["free_blocks"] == result["lifecycle"]["initial_free_blocks"]
            result["post_probe_receipt_audit"] = audit_receipts(lifecycle_rows)
            result["post_probe_cache_state"] = cache_state(cache_api)
            rows = [json.loads(line) for path in (out / "probe").glob("*.jsonl")
                    for line in path.read_text().splitlines()]
            result["kv_integrity"] = audit_probe_sources(rows, lifecycle_rows)
            write_json(out / "kv-audit.json", result["kv_integrity"])
            assert result["kv_integrity"]["passed"], "No equal KV coverage from an orphaned batch"
            print(json.dumps(result["kv_integrity"]), flush=True)
        result["passed"] = True
        print(json.dumps(result["lifecycle"]), flush=True)
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        engine.stop()
        try:
            if cache.process and cache.process.poll() is None:
                result["after_engine_stop"] = cache_state(cache_api)
                write_json(out / "after-engine-stop-status.json", requests.get(cache_api + "/status", timeout=10).json())
                result["passed"] = result["passed"] and drained(result["after_engine_stop"]) and not result["after_engine_stop"]["registered_gpu_ids"]
                if args.observe_after_stop:
                    started = time.monotonic()
                    observations = []
                    while time.monotonic() - started < args.observe_after_stop:
                        time.sleep(min(15, max(0, args.observe_after_stop - (time.monotonic()-started))))
                        state = cache_state(cache_api)
                        observations.append(dict(elapsed_seconds=time.monotonic()-started, state=state))
                        write_json(out / "post-stop-observations.json", observations)
                        print(json.dumps(dict(elapsed_seconds=round(observations[-1]["elapsed_seconds"]),
                            sessions=state["active_sessions"], jobs=state["active_prefetch_jobs"],
                            read_locks=state["l1_read_locked"])), flush=True)
                    result["ttl_final"] = observations[-1]["state"]
                    result["ttl_passed"] = drained(result["ttl_final"]) and not result["ttl_final"]["active_sessions"] and not result["ttl_final"]["registered_gpu_ids"]
                    result["passed"] = result["passed"] and result["ttl_passed"]
        except Exception as exc:
            result.update(passed=False, shutdown_snapshot_error=repr(exc))
        finally:
            cache.stop()
            write_json(out / "result.json", result)


if __name__ == "__main__":
    main()
