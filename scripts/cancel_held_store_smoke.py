"""Abort a live request while a real STORE result is held; no tick request.

The underlying GPU STORE may already be complete. This tests cancellation
and scheduler-owned pin release on a delayed receipt, not interrupted DMA.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import time

import requests

from l2_prefetch_cancel_smoke import events, drained
from lifecycle_smoke import prompt
from natural_preemption_smoke import audit_receipts
from preemption_smoke import completion
from remote_lookup_cancel_smoke import cache_state, open_stream, wait_for
from validate_environment import ROOT, Service, write_json


def audit(rows):
    rows = sorted(rows, key=lambda r:r["monotonic_ns"])
    held = [r for r in rows if r["event"] == "store_receipt_held"]
    assert len(held) == 1, "Expected one held batch before cancellation"
    target = held[0]["request_id"]
    arrivals = [r for r in rows if r["event"] == "request_arrived"]
    assert len(arrivals) == 1 and arrivals[0]["request_id"] == target, "No tick or followup before settlement"
    finished = [r for r in rows if r["event"] == "request_finished" and r["request_id"] == target]
    receipts = [r for r in rows if r["event"] == "scheduler_receipt_before" and r["request_id"] == target]
    delivered = [r for r in rows if r["event"] == "held_store_result_delivered" and r["request_id"] == target]
    assert len(finished) == len(receipts) == len(delivered) == 1
    assert finished[0]["status"] == "FINISHED_ABORTED"
    assert held[0]["monotonic_ns"] < finished[0]["monotonic_ns"] < delivered[0]["monotonic_ns"] <= receipts[0]["monotonic_ns"]
    assert delivered[0]["actual_result"] is True
    pins = audit_receipts(rows)
    baseline = next(r["free_blocks"] for r in rows if r["event"] == "block_pool_bound")
    snapshots = [r for r in rows if r["event"] == "scheduler_snapshot"]
    assert snapshots[-1]["free_blocks"] == baseline
    assert not snapshots[-1]["registered_ids"] and not snapshots[-1]["deferred_frees"]
    assert not snapshots[-1]["tracked_refs"]
    return dict(request_id=target, status="FINISHED_ABORTED", **pins,
                initial_free_blocks=baseline, final_free_blocks=snapshots[-1]["free_blocks"],
                receipt_after_cancel_ms=(receipts[0]["monotonic_ns"]-finished[0]["monotonic_ns"])/1e6)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observe-after-stop", type=float, default=0)
    args = parser.parse_args()
    if not 0 <= args.observe_after_stop <= 900:
        parser.error("Post-stop observation must be in [0,900]")
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} occupied")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    directory = out / "events"
    directory.mkdir()
    os.environ.update(CACHEPILOT_PREEMPTION_DIR=str(directory), CACHEPILOT_STORE_RECEIPT_DELAY="5",
        LMCACHE_TRACK_USAGE="false", LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080",
        KV_CACHE_BYTES="2147483648")
    api, cache_api = "http://127.0.0.1:8000", "http://127.0.0.1:8080"
    model = str(ROOT / "models/Qwen3-4B")
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "cancel-held-store", "--enforce-eager"], out / "vllm.log")
    stream = None
    result = dict(passed=False, controlled_receipt_delay=5, max_deferral_seconds=0.05,
                  eager=True, connector="LateStoreReceiptConnector", interrupted_dma=False,
                  observe_after_stop_seconds=args.observe_after_stop)
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        stream = open_stream("127.0.0.1", model, prompt())
        held = wait_for(lambda: next((r for r in events(directory) if r["event"] == "store_receipt_held"), None), 30)
        assert not any(r["event"] == "request_finished" and r["request_id"] == held["request_id"] for r in events(directory))
        stream.close()
        stream = None
        result["client_closed_unix"] = time.time()
        # No new request is sent until cancellation and receipt have drained.
        def settled():
            rows = events(directory)
            snapshots = sorted((r for r in rows if r["event"] == "scheduler_snapshot"), key=lambda r:r["monotonic_ns"])
            bound = next((r for r in rows if r["event"] == "block_pool_bound"), None)
            return bound and snapshots and snapshots[-1]["free_blocks"] == bound["free_blocks"] and not snapshots[-1]["registered_ids"] and any(r["event"] == "held_store_result_delivered" for r in rows)
        wait_for(settled, 30)
        result["lifecycle"] = audit(events(directory))
        wait_for(lambda: drained(cache_state(cache_api)), 30)
        result["after_cancel"] = cache_state(cache_api)
        write_json(out / "after-cancel-status.json", requests.get(cache_api + "/status", timeout=10).json())
        followup = completion(api, model, "Explain KV caching in one sentence.", 32)
        result["followup_usage"] = followup["usage"]
        assert followup["usage"]["completion_tokens"] == 32
        result["passed"] = True
        print(json.dumps(result["lifecycle"]), flush=True)
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        if stream is not None:
            stream.close()
        engine.stop()
        try:
            if cache.process and cache.process.poll() is None:
                result["after_engine_stop"] = cache_state(cache_api)
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
