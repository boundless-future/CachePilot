"""Cancel a normal-connector request while its MP lookup cannot complete."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import time

import requests

from lifecycle_smoke import cache_summary, metric_summary, prompt
from preemption_smoke import completion
from validate_environment import ROOT, Service, get_text, write_json


def deferred_requests(metrics):
    return sum(float(line.rsplit(" ", 1)[1]) for line in metrics.splitlines()
               if line.startswith("vllm:num_requests_waiting_by_reason{")
               and 'reason="deferred"' in line)


def cache_state(api):
    state = requests.get(api + "/status", timeout=10).json()
    return cache_summary(state) | dict(
        active_prefetch_jobs=state["active_prefetch_jobs"],
        abandoned_prefetch_jobs=state.get("abandoned_prefetch_jobs", 0),
        reclaim_failed_jobs=state.get("reclaim_failed_jobs", 0),
        reclaim_completed_jobs=state.get("reclaim_completed_jobs", 0),
        reclaim_key_snapshots=state.get("reclaim_key_snapshots", 0),
        completed_results_count=state["storage_manager"]["prefetch_controller"]["completed_results_count"],
    )


def wait_for(predicate, timeout, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError("Expected lifecycle state did not appear")


def open_stream(api_host, model, text):
    body = json.dumps(dict(model=model, prompt=text, max_tokens=256,
                           temperature=0, seed=42, ignore_eos=True,
                           stream=True)).encode()
    request = (b"POST /v1/completions HTTP/1.1\r\n"
               + f"Host: {api_host}:8000\r\n".encode()
               + b"Content-Type: application/json\r\n"
               + b"Accept: text/event-stream\r\n"
               + f"Content-Length: {len(body)}\r\n".encode()
               + b"Connection: close\r\n\r\n" + body)
    sock = socket.create_connection((api_host, 8000), timeout=10)
    try:
        sock.sendall(request)
        return sock
    except BaseException:
        sock.close()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-lookup", action="store_true")
    parser.add_argument("--trace-server", action="store_true")
    parser.add_argument("--release-cancelled", action="store_true")
    parser.add_argument("--reclaim-cancelled", action="store_true")
    args = parser.parse_args()
    if args.release_cancelled and not args.trace_server:
        parser.error("--release-cancelled requires --trace-server")
    if args.reclaim_cancelled and not args.trace_server:
        parser.error("--reclaim-cancelled requires --trace-server")
    if args.reclaim_cancelled and args.release_cancelled:
        parser.error("--reclaim-cancelled cannot be combined with --release-cancelled")
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.update(LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080")
    api = "http://127.0.0.1:8000"
    cache_api = "http://127.0.0.1:8080"
    model = str(ROOT / "models/Qwen3-4B")
    text = prompt()
    mode = "lookup-timeline" if args.trace_lookup else "immediate"
    if args.trace_lookup:
        os.environ["CACHEPILOT_LOOKUP_TIMELINE_DIR"] = str(
            args.output.resolve() / "lookup-timeline")
    if args.trace_server:
        os.environ["CACHEPILOT_LOOKUP_SERVER_TIMELINE_DIR"] = str(
            args.output.resolve() / "server-timeline")
    if args.release_cancelled:
        os.environ["CACHEPILOT_LOOKUP_SERVER_RELEASE_CANCELLED"] = "1"
    if args.reclaim_cancelled:
        os.environ["CACHEPILOT_LOOKUP_SERVER_RECLAIM"] = "1"
    command = ["bash", str(ROOT / "scripts/serve.sh"), mode]
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")],
                    args.output / "lmcache.log")
    engine = Service(command, args.output / "vllm-warmup.log")
    result = dict(model=model, prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                  prompt_chars=len(text), connector=("LookupTimelineConnector"
                                                       if args.trace_lookup else "LMCacheMPConnector"),
                  server_timeline_enabled=args.trace_server,
                  diagnostic_release_enabled=args.release_cancelled,
                  candidate_reclaim_enabled=args.reclaim_cancelled,
                  timestamps_unix={})
    paused = False
    stream = None
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        reference = completion(api, model, text, 32)
        result["reference_usage"] = reference["usage"]
        time.sleep(3)
        engine.stop()
        wait_for(lambda: not requests.get(cache_api + "/status", timeout=5)
                 .json()["registered_gpu_ids"], 30)
        engine = Service(command, args.output / "vllm-lookup.log")
        engine.start(api + "/health")
        result["cache_status_before"] = cache_state(cache_api)
        result["metrics_before"] = metric_summary(get_text(api + "/metrics"))
        if result["cache_status_before"]["l1_read_locked"]:
            raise AssertionError("Warmup left read locks before lookup probe")
        if deferred_requests(get_text(api + "/metrics")):
            raise AssertionError("Unexpected deferred requests before lookup probe")

        os.killpg(cache.process.pid, signal.SIGSTOP)
        paused = True
        result["timestamps_unix"]["server_paused"] = time.time()
        stream = open_stream("127.0.0.1", model, text)
        result["timestamps_unix"]["request_submitted"] = time.time()
        result["deferred_while_paused"] = wait_for(
            lambda: deferred_requests(get_text(api + "/metrics")), 15)
        result["timestamps_unix"]["deferred_observed"] = time.time()
        stream.close()
        stream = None
        result["timestamps_unix"]["client_disconnected"] = time.time()
        result["deferred_cleared_after_disconnect"] = wait_for(
            lambda: deferred_requests(get_text(api + "/metrics")) == 0, 15)
        result["timestamps_unix"]["deferred_cleared"] = time.time()
        os.killpg(cache.process.pid, signal.SIGCONT)
        paused = False
        result["timestamps_unix"]["server_resumed"] = time.time()
        wait_for(lambda: requests.get(cache_api + "/status", timeout=5)
                 .status_code == 200, 15)
        if args.reclaim_cancelled:
            wait_for(lambda: cache_state(cache_api)["reclaim_completed_jobs"] >= 1, 15)
        time.sleep(2)
        result["cache_status_after_cancel"] = cache_state(cache_api)
        result["timestamps_unix"]["cancel_status_checked"] = time.time()
        result["metrics_after_cancel"] = metric_summary(get_text(api + "/metrics"))
        follow_up = completion(api, model, text, 32)
        result["follow_up_usage"] = follow_up["usage"]
        result["timestamps_unix"]["follow_up_completed"] = time.time()
        result["outputs_equal"] = follow_up["text"] == reference["text"]
        result["metrics_after_follow_up"] = metric_summary(get_text(api + "/metrics"))
        result["cache_status_after_follow_up"] = cache_state(cache_api)
        if (not result["outputs_equal"] or
                result["metrics_after_follow_up"]["external_hit_tokens"] <=
                result["metrics_after_cancel"]["external_hit_tokens"]):
            raise AssertionError("Follow-up did not retrieve the saved prefix")
        status = result["cache_status_after_cancel"]
        if (not status["is_healthy"] or status["active_prefetch_jobs"] or
                status["abandoned_prefetch_jobs"] or status["reclaim_failed_jobs"] or
                status["reclaim_key_snapshots"] or status["completed_results_count"] or
                status["l1_read_locked"] or
                status["l1_write_locked"] or status["store_pending"] or
                status["store_in_flight"] or status["prefetch_in_flight"] or
                status["prefetch_pending"] or status["prefetch_lookup"] or
                status["prefetch_load"]):
            raise AssertionError("Cancelled lookup left LMCache resources active")
        result["passed"] = True
    except Exception as exc:
        result.update(passed=False, error=repr(exc))
        raise
    finally:
        if stream is not None:
            stream.close()
        if paused:
            os.killpg(cache.process.pid, signal.SIGCONT)
        engine.stop()
        if cache.process and cache.process.poll() is None:
            try:
                result["cache_status_after_engine_stop"] = cache_state(cache_api)
            except requests.RequestException as exc:
                result["cache_status_after_engine_stop_error"] = repr(exc)
        cache.stop()
        write_json(args.output / "result.json", result)


if __name__ == "__main__":
    main()
