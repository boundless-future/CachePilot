"""Exercise a real MP-server STORE rejection and its worker/scheduler receipt."""

import argparse
import hashlib
import os
from pathlib import Path
import socket
import time

import requests

from lifecycle_smoke import cache_summary, metric_summary, prompt
from preemption_smoke import completion
from store_failure_smoke import summarize
from validate_environment import ROOT, Service, get_text, write_json


def settled(status):
    return (status["is_healthy"] and not status["store_pending"] and
            not status["store_in_flight"] and not status["prefetch_in_flight"] and
            not status["prefetch_pending"] and not status["prefetch_lookup"] and
            not status["prefetch_load"] and not status["l1_read_locked"] and
            not status["l1_write_locked"])


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
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")],
                    args.output / "lmcache.log")
    command = ["bash", str(ROOT / "scripts/serve.sh"), "server-rejected-store"]
    engine = Service(command, args.output / "vllm.log")
    result = dict(model=model, prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                  failure_mode="server rejects one underflowed STORE")
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        target = completion(api, model, text, 256)
        write_json(args.output / "target.json", target)
        result["target_usage"] = target["usage"]
        if target["usage"]["completion_tokens"] != 256:
            raise AssertionError("Target generation did not complete")
        time.sleep(2)
        result["lifecycle"] = summarize(events, mode="server-reject")
        rejection = ("STORE block ID underflow for request_id=" +
                     result["lifecycle"]["request_id"])
        if rejection not in (args.output / "lmcache.log").read_text(errors="replace"):
            raise AssertionError("MP server did not log the expected STORE rejection")
        result["server_rejection_logged"] = True
        result["cache_status_before_restart"] = cache_summary(
            requests.get(cache_api + "/status", timeout=20).json())
        if not settled(result["cache_status_before_restart"]):
            raise AssertionError("LMCache resources did not settle after rejected STORE")

        engine.stop()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = requests.get(cache_api + "/status", timeout=5).json()
            if not state.get("registered_gpu_ids"):
                break
            time.sleep(0.5)
        else:
            raise AssertionError("GPU registration did not clear before restart")
        engine = Service(command, args.output / "vllm-restart.log")
        engine.start(api + "/health")
        before = metric_summary(get_text(api + "/metrics"))
        follow_up = completion(api, model, text, 32)
        write_json(args.output / "follow-up.json", follow_up)
        result["follow_up_usage"] = follow_up["usage"]
        result["follow_up_external_hit_delta"] = (
            metric_summary(get_text(api + "/metrics"))["external_hit_tokens"] -
            before["external_hit_tokens"])
        if (follow_up["usage"]["completion_tokens"] != 32 or
                result["follow_up_external_hit_delta"] != 0):
            raise AssertionError("Rejected STORE left an externally reusable prefix")
        time.sleep(2)
        result["cache_status_after_restart"] = cache_summary(
            requests.get(cache_api + "/status", timeout=20).json())
        if not settled(result["cache_status_after_restart"]):
            raise AssertionError("LMCache resources did not settle after restart")
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
