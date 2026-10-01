"""A real native terminal is deliberately withheld; retained buffers must block close.

This tests a negative lifecycle boundary, not automatic recovery or a driver fault.
The owned test process is discarded after documenting the unresolved state.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import time

import requests

from l2_prefetch_cancel_smoke import events
from lifecycle_smoke import prompt
from preemption_smoke import completion
from remote_lookup_cancel_smoke import cache_state, wait_for
from validate_environment import ROOT, Service, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("store", "retrieve"), required=True)
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
        CACHEPILOT_OWNED_HOST_EXCEPTION="0", CACHEPILOT_OWNED_DROP_TERMINAL="1",
        CACHEPILOT_OWNED_HOLD_CYCLES="0")
    api, cache_api = "http://127.0.0.1:8000", "http://127.0.0.1:8080"
    command = ["bash", str(ROOT / "scripts/serve.sh"), "owned-service", "--enforce-eager"]
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    engine = Service(command, out / "vllm-warmup.log")
    result = dict(passed=False, kind=args.kind, expected_unresolved=True,
        injection="withhold one real native terminal notification after completed CUDA copy",
        driver_fault=False, automatic_recovery=False, recovery_boundary="whole instance restart")
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        model, text = str(ROOT / "models/Qwen3-4B"), prompt()
        reference = completion(api, model, text, 32)
        engine.stop()
        if args.kind == "store":
            cache.stop()
            cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache-unresolved.log")
            cache.start(cache_api + "/status")
        engine = Service(command, out / "vllm-unresolved.log")
        engine.start(api + "/health")
        (directory / "armed-transfer").touch()
        output = completion(api, model, text, 32)
        assert output["text"] == reference["text"]
        result["output_equal"] = True
        wait_for(lambda: any(r["event"] == "diagnostic_transfer_close_refused" for r in events(directory)), 20)
        copy, = [r for r in events(directory) if r["event"] == "diagnostic_copy_enqueued"]
        result["original_transfer"] = dict(request_id=copy["request_id"], sequence=copy["sequence"])
        engine.stop()
        # END and context removal cannot retire a consumer with missing proof.
        time.sleep(2)
        raw = requests.get(cache_api + "/status", timeout=10).json()
        write_json(out / "unresolved-status.json", raw)
        state = cache_state(cache_api)
        result["retained_state"] = state
        assert state["owned_transfers"] == 1
        assert state["owned_deferred_contexts"] == 1
        if args.kind == "store":
            assert state["l1_write_locked"] > 0
        else:
            assert state["owned_read_pins"] == 17 and state["l1_read_locked"] == 17
        cache.stop()
        rows = events(directory)
        same = lambda r: r.get("request_id") == copy["request_id"] and r.get("sequence") == copy["sequence"]
        assert not any(r["event"] in ("terminal_seen", "transfer_retired") and same(r) for r in rows)
        blocked = [r for r in rows if r["event"] == "owned_shutdown_blocked"]
        assert blocked and blocked[-1]["owned_transfers"] == 1
        active_pid = next(path for path in directory.glob("owned-service-*.jsonl")
                          if any(json.loads(line)["event"] == "diagnostic_terminal_dropped"
                                 for line in path.read_text().splitlines()))
        assert not any(json.loads(line)["event"] == "owned_shutdown_drained"
                       for line in active_pid.read_text().splitlines())
        result.update(passed=True, original_buffer_retained=True, shutdown_refused=True,
                      direct_transfer_close_refused=True, process_discarded=True)
        print(json.dumps(result), flush=True)
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        engine.stop()
        cache.stop()
        write_json(out / "result.json", result)


if __name__ == "__main__":
    main()
