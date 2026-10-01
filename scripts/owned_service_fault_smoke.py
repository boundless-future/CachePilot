"""Real service STORE/RETRIEVE enqueue-error and pending-stream cancellation."""
import argparse
import json
import os
from pathlib import Path
import socket
import time

import requests

from l2_prefetch_cancel_smoke import drained, events
from lifecycle_smoke import metric_summary, prompt
from preemption_smoke import completion
from remote_lookup_cancel_smoke import cache_state, open_stream, wait_for
from validate_environment import ROOT, Service, get_text, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=("store", "retrieve"))
    parser.add_argument("--cancel", action="store_true")
    args = parser.parse_args()
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} occupied")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    directory = out / "events"
    directory.mkdir()
    os.environ.update(CACHEPILOT_OWNED_SERVICE_DIR=str(directory),
        CACHEPILOT_OWNED_MP_DIR=str(directory), CACHEPILOT_OWNED_FAULT_KIND=args.kind,
        CACHEPILOT_OWNED_HOLD_CYCLES="5000000000" if args.cancel else "0")
    api, cache_api = "http://127.0.0.1:8000", "http://127.0.0.1:8080"
    engine_cmd = ["bash", str(ROOT / "scripts/serve.sh"), "owned-service", "--enforce-eager"]
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    engine = Service(engine_cmd, out / "vllm-first.log")
    model, text = str(ROOT / "models/Qwen3-4B"), prompt()
    result = dict(passed=False, kind=args.kind, cancel=args.cancel,
        fault="host exception after real CUDA copy enqueue", driver_fault=False,
        pending_stream_hold=args.cancel, hold_position="after_real_copy_submit",
        copy_execution_overlap_proven=False, literal_dma_interruption=False)
    stream = None

    def snapshot(name):
        raw = requests.get(cache_api + "/status", timeout=10).json()
        write_json(out / f"{name}-status.json", raw)
        state = cache_state(cache_api)
        result[name] = state
        return state

    def settled():
        rows = [r for r in events(directory) if r["event"] == "scheduler_snapshot"]
        return rows and not rows[-1]["registered_ids"] and not rows[-1]["tracked_refs"] and not rows[-1]["deferred_frees"] and rows[-1]["free_blocks"] == 909 and drained(cache_state(cache_api))

    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        # Reference is a cold compute with injection disabled. Restart cache
        # as well for STORE so the diagnostic cannot skip an existing object.
        reference = completion(api, model, text, 32)
        wait_for(settled, 20)
        engine.stop()
        if args.kind == "store":
            cache.stop()
            cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache-fault.log")
            cache.start(cache_api + "/status")
        engine = Service(engine_cmd, out / "vllm-fault.log")
        engine.start(api + "/health")
        (directory / "armed-transfer").touch()
        if args.cancel:
            stream = open_stream("127.0.0.1", model, text)
            wait_for(lambda: (directory / "copy-enqueued").exists(), 30)
            # The terminal must still be pending when cancellation is sent.
            held = snapshot("pending_stream")
            assert held["owned_transfers"] >= 1
            rows = events(directory)
            copy, = [r for r in rows if r["event"] == "diagnostic_copy_enqueued"]
            assert not any(r["event"] in ("terminal_seen", "transfer_retired")
                           and r["request_id"] == copy["request_id"]
                           and r["sequence"] == copy["sequence"] for r in rows)
            result["pending_original_transfer"] = dict(request_id=copy["request_id"],
                                                       sequence=copy["sequence"])
            result["cancel_unix_time"] = time.time()
            stream.close()
            stream = None
        else:
            failed = completion(api, model, text, 32)
            result["recomputed_output_equal"] = failed["text"] == reference["text"]
            assert result["recomputed_output_equal"]
        wait_for(settled, 30)
        snapshot("after_fault")
        rows = events(directory)
        injected = [r for r in rows if r["event"] == "diagnostic_copy_enqueued"]
        assert len(injected) == 1
        fault = injected[0]
        terminals = [r for r in rows if r["event"] == "transfer_retired" and r["sequence"] == fault["sequence"] and r["request_id"] == fault["request_id"]]
        assert len(terminals) == 1 and terminals[0]["succeeded"] is False
        assert terminals[0]["unix_time"] > fault["unix_time"]
        if args.cancel:
            assert terminals[0]["unix_time"] > result["cancel_unix_time"]
        result["actual_server_false"] = True
        result["terminal_audit"] = terminals[0]
        engine.stop()
        wait_for(lambda: not cache_state(cache_api)["registered_gpu_ids"], 20)
        snapshot("after_fault_engine_stop")
        engine = Service(engine_cmd, out / "vllm-recovery.log")
        engine.start(api + "/health")
        metrics = metric_summary(get_text(api + "/metrics"))
        recovered = completion(api, model, text, 32)
        result["recovery_output_equal"] = recovered["text"] == reference["text"]
        result["recovery_external_hit_tokens"] = metric_summary(get_text(api + "/metrics"))["external_hit_tokens"] - metrics["external_hit_tokens"]
        assert result["recovery_output_equal"]
        if args.kind == "store":
            assert result["recovery_external_hit_tokens"] == 0
        else:
            assert result["recovery_external_hit_tokens"] == 4352
        wait_for(settled, 20)
        engine.stop()
        final = snapshot("final")
        assert drained(final) and not final["registered_gpu_ids"]
        result["passed"] = True
        print(json.dumps({k: result[k] for k in ("passed", "kind", "cancel", "actual_server_false", "recovery_external_hit_tokens")}), flush=True)
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        if stream:
            stream.close()
        engine.stop()
        cache.stop()
        write_json(out / "result.json", result)


if __name__ == "__main__":
    main()
