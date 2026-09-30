"""Real partial filesystem writes; L2 failure is separate from worker STORE."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time

import requests

from l2_prefetch_cancel_smoke import events
from lifecycle_smoke import prompt, metric_summary
from preemption_smoke import completion
from remote_lookup_cancel_smoke import cache_state, wait_for
from validate_environment import ROOT, Service, get_text, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} occupied")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    directory, disk = out / "events", out / "kv-files"
    directory.mkdir()
    os.environ.update(CACHEPILOT_WRITE_FAULT_DIR=str(directory),
                      CACHEPILOT_WRITE_FAULT_DISK=str(disk),
                      LMCACHE_TRACK_USAGE="false")
    api, cache_api = "http://127.0.0.1:8000", "http://127.0.0.1:8080"
    cache_args = ["server", "--host", "127.0.0.1", "--port", "5555", "--http-host",
        "127.0.0.1", "--http-port", "8080", "--l1-size-gb", "16", "--l1-init-size-gb", "16",
        "--eviction-policy", "LRU", "--enable-extra-logging", "--l2-adapter",
        json.dumps(dict(type="fs", base_path=str(disk)))]
    engine_cmd = ["bash", str(ROOT / "scripts/serve.sh"), "immediate", "--enforce-eager"]
    cache = Service([sys.executable, str(ROOT / "scripts/l2_write_fault_server.py"), *cache_args],
                    out / "lmcache-fault.log")
    engine = Service(engine_cmd, out / "vllm-fault.log")
    result = dict(passed=False, fault="RLIMIT_FSIZE EFBIG", connector="LMCacheMPConnector",
                  telemetry_enabled=False, eager=True, worker_failure_test=False)

    def snapshot(name):
        write_json(out / f"{name}-status.json", requests.get(cache_api + "/status", timeout=10).json())
        state = cache_state(cache_api)
        result[name] = state
        return state

    def settled():
        state = cache_state(cache_api)
        return all(state[k] == 0 for k in ("store_pending", "store_in_flight", "prefetch_in_flight",
                    "l1_read_locked", "l1_write_locked"))

    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        (directory / "armed").touch()
        reference = completion(api, str(ROOT / "models/Qwen3-4B"), prompt(), 32)
        wait_for(lambda: any(e["event"] == "actual_store_result" for e in events(directory)), 30)
        wait_for(settled, 30)
        snapshot("after_fault")
        rows = events(directory)
        limits = [r for r in rows if r["event"] == "limit_enter"]
        failures = [r for r in rows if r["event"] == "actual_store_result" and not r["success"]]
        partials = [r for r in rows if r["event"] == "partial_tmp_before_unlink"]
        assert len(limits) == len(failures) == 1
        assert limits[0]["task_id"] == failures[0]["task_id"]
        # Immediate offload can split one prompt into several STORE batches.
        # Audit the actual injected batch, never assume it contains all 17.
        failed_objects = limits[0]["object_count"]
        assert len(partials) == failed_objects > 0
        assert all(size > 1024 * 1024 for size in limits[0]["expected_object_bytes"])
        assert all(r["bytes"] == 1024 * 1024 for r in partials)
        assert not list(disk.glob("*.tmp"))
        assert all(not (disk / Path(r["filename"]).with_suffix(".data")).exists() for r in partials)
        final_files = len(list(disk.glob("*.data")))
        assert final_files + failed_objects == 17
        result["fault_evidence"] = dict(objects=failed_objects, partial_bytes_each=1024*1024,
            failed_task=failures[0], final_files=final_files, temporary_files=0)
        engine.stop()
        snapshot("after_engine_stop")
        cache.stop()
        # Fresh unmodified server, same disk: failed L2 writes must not hit.
        cache = Service(["lmcache", *cache_args], out / "lmcache-recovery.log")
        engine = Service(engine_cmd, out / "vllm-recovery.log")
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        before = metric_summary(get_text(api + "/metrics"))
        recovered = completion(api, str(ROOT / "models/Qwen3-4B"), prompt(), 32)
        result["external_hit_tokens_after_restart"] = metric_summary(get_text(api + "/metrics"))["external_hit_tokens"] - before["external_hit_tokens"]
        result["outputs_equal"] = reference["text"] == recovered["text"]
        wait_for(lambda: len(list(disk.glob("*.data"))) == 17 and settled(), 30)
        snapshot("after_recovery")
        assert result["outputs_equal"] and result["external_hit_tokens_after_restart"] == 0
        result["recovery_files"] = [dict(name=p.name, bytes=p.stat().st_size) for p in sorted(disk.glob("*.data"))]
        assert all(p["bytes"] == 37748736 for p in result["recovery_files"])
        result["passed"] = True
        print(json.dumps({k: result[k] for k in ("passed", "outputs_equal", "external_hit_tokens_after_restart")}), flush=True)
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        engine.stop()
        cache.stop()
        write_json(out / "result.json", result)


if __name__ == "__main__":
    main()
