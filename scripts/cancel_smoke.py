"""Smoke-test client cancellation against the real vLLM/LMCache path.

This is a lifecycle probe, not a performance benchmark.  It closes one
stream after the first generated chunk, waits for connector cleanup, then
sends a normal follow-up request and records service state and metrics.
"""
import argparse
import json
import os
from pathlib import Path
import time

import requests

from validate_environment import API, ROOT, Service, get_text, write_json


def _prompt(repetitions: int = 900) -> str:
    sentence = (
        "This is a cancellation lifecycle probe. Preserve the prefix and "
        "continue the deterministic analysis of the cached context."
    )
    return " ".join(f"Evidence {i}: {sentence}" for i in range(repetitions))


def _cancel_stream(api: str, model: str, prompt: str) -> dict:
    payload = dict(
        model=model,
        prompt=prompt,
        max_tokens=512,
        temperature=0,
        seed=42,
        ignore_eos=True,
        stream=True,
        stream_options={"include_usage": True},
    )
    started = time.monotonic()
    chunks = 0
    status = None
    error = None
    response = None
    try:
        response = requests.post(api + "/v1/completions", json=payload,
                                 stream=True, timeout=(10, 180))
        status = response.status_code
        response.raise_for_status()
        for raw in response.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            chunks += 1
            # Close before consuming the stream's DONE marker to force a
            # client disconnect while the request is still active.
            break
    except Exception as exc:  # record the client-side result for diagnosis
        error = repr(exc)
    finally:
        if response is not None:
            response.close()
    return dict(status=status, chunks_before_close=chunks,
                client_error=error,
                elapsed_ms=(time.monotonic() - started) * 1000)


def _follow_up(api: str, model: str, prompt: str) -> dict:
    payload = dict(model=model, prompt=prompt + "\nFollow-up:", max_tokens=16,
                   temperature=0, seed=42, ignore_eos=True)
    started = time.monotonic()
    response = requests.post(api + "/v1/completions", json=payload, timeout=180)
    data = response.json()
    if response.status_code >= 400:
        return dict(status=response.status_code, error=data,
                    elapsed_ms=(time.monotonic() - started) * 1000)
    return dict(status=response.status_code,
                usage=data.get("usage"),
                elapsed_ms=(time.monotonic() - started) * 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    os.environ.update(LMCACHE_PORT="5556", LMCACHE_HTTP_PORT="8081",
                      VLLM_SERVER_DEV_MODE="1")
    api = "http://127.0.0.1:8000"
    cache_api = "http://127.0.0.1:8081"
    model = str(ROOT / "models/Qwen3-4B")
    # Keep the follow-up within the 8192-token max-model-len while leaving
    # enough context to exercise prefix/cache lifecycle.
    prompt = _prompt(260)
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "eviction"],
                     args.output / "vllm.log")
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")],
                    args.output / "lmcache.log")
    result = dict(model=model, prompt_tokens=None, wait_seconds=args.wait_seconds)
    try:
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        warmup = requests.post(api + "/v1/completions", json=dict(
            model=model, prompt="Warm up cancellation smoke.", max_tokens=4,
            temperature=0, ignore_eos=True), timeout=60)
        warmup.raise_for_status()
        result["cancel"] = _cancel_stream(api, model, prompt)
        result["metrics_before_wait"] = get_text(api + "/metrics")
        time.sleep(args.wait_seconds)
        result["cache_status_after_wait"] = requests.get(
            cache_api + "/status", timeout=15).json()
        result["follow_up"] = _follow_up(api, model, prompt)
        result["metrics_after_follow_up"] = get_text(api + "/metrics")
        result["cache_status_after_follow_up"] = requests.get(
            cache_api + "/status", timeout=15).json()
    except Exception as exc:
        result["fatal_error"] = repr(exc)
    finally:
        engine.stop()
        cache.stop()
        write_json(args.output / "result.json", result)


if __name__ == "__main__":
    main()
