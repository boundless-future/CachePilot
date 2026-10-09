"""Serial eager baseline/off/zero comparison, no hold, failure or policy delay.

The bounded tail uses <256-token requests to evict pending lazy chunks without
creating fresh STORE chunks. Tail traffic is part of the workload, not an idle
drain repair. All state is checked before engine shutdown.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import time

import requests
from transformers import AutoTokenizer

from audit_owned_service import audit as audit_ownership
from audit_protection_handoff import audit as audit_handoff
from l2_prefetch_cancel_smoke import drained
from remote_lookup_cancel_smoke import cache_state
from validate_environment import ROOT, Service, API, CACHE, PROMPT, snapshot, counter, write_json


def run(out, mode):
    out.mkdir(parents=True, exist_ok=False)
    events, probe = out / "events", out / "probe"
    events.mkdir()
    probe.mkdir()
    # The experiment has a clean diagnostic environment, not inherited holds.
    for key in list(os.environ):
        if key.startswith("CACHEPILOT_"):
            del os.environ[key]
    os.environ.update(CACHEPILOT_OWNED_SERVICE_DIR=str(events),
        CACHEPILOT_OWNED_MP_DIR=str(events), CACHEPILOT_HANDOFF_DIR=str(events),
        CACHEPILOT_PROBE_DIR=str(probe), KV_CACHE_BYTES="2147483648")
    config = json.loads((ROOT / "configs/protection-zero.json").read_text())
    config["kv_connector_module_path"] = "scripts.protection_probe_connector"
    config["kv_connector"] = "ObservedEvictionProbeConnector" if mode == "baseline" else "ProtectionProbeConnector"
    config["kv_connector_extra_config"]["cachepilot.protection.enabled"] = mode == "zero"
    write_json(out / "config.json", config)
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "baseline", "--enforce-eager",
        "--kv-transfer-config", json.dumps(config)], out / "vllm.log")
    result = dict(passed=False, mode=mode, eager=True, artificial_hold=False, protection_budget=0)
    responses = []
    tokenizer = AutoTokenizer.from_pretrained(ROOT / "models/Qwen3-4B")

    def send(name, prompt, max_tokens=32):
        payload = dict(model=str(ROOT / "models/Qwen3-4B"), prompt=prompt,
                       temperature=0, seed=42, max_tokens=max_tokens, ignore_eos=True)
        response = requests.post(API + "/v1/completions", json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        responses.append(dict(name=name, payload=payload, response=data))
        write_json(out / "responses.json", responses)
        return data["choices"][0]["text"]

    def state():
        return cache_state(CACHE)

    def wait_drained(name):
        deadline = time.monotonic() + 20
        while True:
            current = state()
            write_json(out / (name + ".json"), current)
            if drained(current):
                return current
            if time.monotonic() >= deadline:
                raise RuntimeError("Running service did not drain: " + name)
            time.sleep(.2)

    try:
        print(f"[{mode}] starting", flush=True)
        cache.start(CACHE + "/status")
        engine.start(API + "/health")
        cold = send("cold", PROMPT)
        assert send("hot", PROMPT) == cold
        # First-token differences create distinct prefixes and real pressure.
        for i in range(12):
            send(f"pressure-{i:02d}", f"Workload-{i}: " + PROMPT)
        print(f"[{mode}] pressure complete; bounded short-request tail", flush=True)
        for i in range(80):
            ids = tokenizer.encode(f"Tail-{i}: " + PROMPT)[:224]
            assert len(ids) == 224
            send(f"tail-{i:02d}", ids, 1)
        result["before_restore"] = wait_drained("before-restore-status")
        before, _ = snapshot(out, "before-restore")
        restored = send("restored", PROMPT)
        after, _ = snapshot(out, "after-restore")
        result["external_hit_tokens"] = counter(after, "vllm:external_prefix_cache_hits_total") - counter(before, "vllm:external_prefix_cache_hits_total")
        result["gpu_hit_tokens"] = counter(after, "vllm:prefix_cache_hits_total") - counter(before, "vllm:prefix_cache_hits_total")
        assert result["external_hit_tokens"] >= 1536 and result["gpu_hit_tokens"] == 0
        assert restored == cold
        assert send("restored-hot", PROMPT) == cold
        result["running_final"] = wait_drained("running-final-status")
        engine.stop()
        result["after_engine_stop"] = wait_drained("after-engine-stop-status")
        assert not result["after_engine_stop"]["registered_gpu_ids"]
        cache.stop()
        result["ownership"] = audit_ownership(out)
        result["handoff"] = audit_handoff(events)
        assert result["handoff"]["failed_batches"] == 0
        if mode != "baseline":
            assert result["handoff"]["bypass_decisions"] > 0
        kv = [json.loads(line) for p in probe.glob("kv-*.jsonl") for line in p.read_text().splitlines()]
        from analyze_kv_probe import summarize_probe
        result["kv"] = summarize_probe(kv)
        assert result["kv"]["covered_and_equal"]
        result.update(passed=True, requests=len(responses))
        print(f"[{mode}] PASS: {result['handoff']['batches']} batches; {result['kv']['counts']}", flush=True)
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        engine.stop()
        if cache.process and cache.process.poll() is None:
            try:
                write_json(out / "failure-final-status.json", state())
            except requests.RequestException:
                pass
        cache.stop()
        write_json(out / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=("baseline", "disabled", "zero"),
                        default=["baseline", "disabled", "zero"])
    args = parser.parse_args()
    for port in (8000, 8080, 5555):
        with socket.socket() as s:
            assert s.connect_ex(("127.0.0.1", port)) != 0, ("occupied port", port)
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for mode in args.modes:
        results.append(run((args.output / mode).resolve(), mode))
        write_json(args.output / "summary.json", results)
    if len(results) > 1:
        reference = json.loads((args.output / args.modes[0] / "responses.json").read_text())
        for mode, result in zip(args.modes[1:], results[1:]):
            other = json.loads((args.output / mode / "responses.json").read_text())
            assert [(r["name"], r["payload"], r["response"]["choices"][0]["text"]) for r in reference] == [
                (r["name"], r["payload"], r["response"]["choices"][0]["text"]) for r in other]
            assert result["handoff"]["store_signatures"] == results[0]["handoff"]["store_signatures"], "Default STORE ranges differ"
        write_json(args.output / "comparison.json", dict(passed=True, modes=args.modes,
            same_payloads_and_outputs=True, same_default_store_ranges=True, performance_experiment=False))


if __name__ == "__main__":
    main()
