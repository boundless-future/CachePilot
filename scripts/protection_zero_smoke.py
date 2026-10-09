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


def run(out, mode, bounded_workload=False, receipt_probe=None, recovery_traffic=False):
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
    config["kv_connector_extra_config"]["cachepilot.protection.enabled"] = mode in ("zero", "two")
    config["kv_connector_extra_config"]["cachepilot.protection.max_chunks"] = 2 if mode == "two" else 0
    if receipt_probe:
        os.environ["CACHEPILOT_PROTECTED_RECEIPT_PROBE"] = receipt_probe
        config["kv_connector_module_path"] = "scripts.protection_receipt_probe"
        config["kv_connector"] = "ReceiptProbeConnector"
    write_json(out / "config.json", config)
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "baseline", "--enforce-eager",
        *(["--no-async-scheduling"] if bounded_workload else []),
        "--kv-transfer-config", json.dumps(config)], out / "vllm.log")
    result = dict(passed=False, mode=mode, eager=True, artificial_hold=False,
        protection_budget=2 if mode == "two" else 0, bounded_workload=bounded_workload)
    result["receipt_probe"] = receipt_probe
    result["artificial_hold"] = receipt_probe == "delayed"
    result["recovery_traffic"] = recovery_traffic
    responses = []
    tokenizer = AutoTokenizer.from_pretrained(ROOT / "models/Qwen3-4B")
    target = tokenizer.encode(PROMPT)[:576] if bounded_workload else PROMPT

    def send(name, prompt, max_tokens=32):
        if bounded_workload and max_tokens != 1:
            max_tokens = 8
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
        cold = send("cold", target)
        assert send("hot", target) == cold
        # First-token differences create distinct prefixes and real pressure.
        for i in range(12):
            prompt = f"Workload-{i}: " + PROMPT
            send(f"pressure-{i:02d}", tokenizer.encode(prompt)[:576] if bounded_workload else prompt)
        print(f"[{mode}] pressure complete; bounded short-request tail", flush=True)
        for i in range(160 if bounded_workload else 80):
            ids = tokenizer.encode(f"Tail-{i}: " + PROMPT)[:224]
            assert len(ids) == 224
            send(f"tail-{i:02d}", ids, 1)
        if recovery_traffic:
            # Explicit separate recovery workload after the fixed 5s receipt
            # hold. This cannot be used as evidence for idle draining.
            time.sleep(6)
            write_json(out / "before-recovery-traffic-status.json", state())
            for i in range(160):
                ids = tokenizer.encode(f"Recovery-tail-{i}: " + PROMPT)[:224]
                send(f"recovery-tail-{i:02d}", ids, 1)
        result["before_restore"] = wait_drained("before-restore-status")
        before, _ = snapshot(out, "before-restore")
        restored = send("restored", target)
        after, _ = snapshot(out, "after-restore")
        result["external_hit_tokens"] = counter(after, "vllm:external_prefix_cache_hits_total") - counter(before, "vllm:external_prefix_cache_hits_total")
        result["gpu_hit_tokens"] = counter(after, "vllm:prefix_cache_hits_total") - counter(before, "vllm:prefix_cache_hits_total")
        assert result["external_hit_tokens"] >= (512 if bounded_workload else 1536) and result["gpu_hit_tokens"] == 0
        assert restored == cold
        assert send("restored-hot", target) == cold
        if bounded_workload:
            # Replay every original pressure prefix in BOTH modes. This makes
            # real protected STORE content eligible for H2D/bitwise validation,
            # even if a different candidate is selected on another run.
            originals = [r for r in responses if r["name"].startswith("pressure-")]
            for row in originals:
                assert send("replay-" + row["name"], row["payload"]["prompt"]) == row["response"]["choices"][0]["text"]
            # A missed prefix may create a new pending STORE. This fixed tail
            # is identical across modes and is not an idle-drain repair.
            for i in range(160):
                ids = tokenizer.encode(f"Final-tail-{i}: " + PROMPT)[:224]
                send(f"final-tail-{i:02d}", ids, 1)
        result["running_final"] = wait_drained("running-final-status")
        engine.stop()
        result["after_engine_stop"] = wait_drained("after-engine-stop-status")
        assert not result["after_engine_stop"]["registered_gpu_ids"]
        cache.stop()
        result["ownership"] = audit_ownership(out)
        result["handoff"] = audit_handoff(events, allow_protection=mode == "two")
        assert result["handoff"]["failed_batches"] == (1 if receipt_probe == "failed-after-success" else 0)
        if mode not in ("baseline", "two"):
            assert result["handoff"]["bypass_decisions"] > 0
        if mode == "two":
            from audit_bounded_protection import audit as audit_bounded
            result["protection"] = audit_bounded(events)
        kv = [json.loads(line) for p in probe.glob("kv-*.jsonl") for line in p.read_text().splitlines()]
        from analyze_kv_probe import summarize_probe
        result["kv"] = summarize_probe(kv)
        assert result["kv"]["covered_and_equal"]
        if mode == "two":
            handoffs = [json.loads(line) for p in events.glob("handoff-*.jsonl") for line in p.read_text().splitlines()]
            protected = {r["prefix_sha256"] for r in handoffs if r["event"] == "protection_metadata_committed"}
            restored_prefixes = {r["token_prefix_sha256"] for r in kv if r["phase"] == "after_retrieve"}
            result["protected_prefixes_retrieved"] = sorted(protected & restored_prefixes)
            result["protected_prefixes_not_retrieved"] = sorted(protected - restored_prefixes)
            assert result["protected_prefixes_retrieved"], "No protected STORE prefix was actually restored"
            from audit_bounded_kv import audit as audit_sources
            result["strict_protected_sources"] = audit_sources(out)
            if receipt_probe:
                from audit_protected_receipt import audit_rows as audit_receipt
                result["receipt_probe_audit"] = audit_receipt(handoffs, receipt_probe)
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
    parser.add_argument("--bounded-workload", action="store_true")
    parser.add_argument("--receipt-probe", choices=("delayed", "failed-after-success"))
    parser.add_argument("--recovery-traffic", action="store_true")
    parser.add_argument("--modes", nargs="+", choices=("baseline", "disabled", "zero", "two"),
                        default=["baseline", "disabled", "zero"])
    args = parser.parse_args()
    if "two" in args.modes and not args.bounded_workload:
        parser.error("two-chunk mode requires --bounded-workload")
    if args.receipt_probe and (args.modes != ["two"] or not args.bounded_workload):
        parser.error("receipt probes require --bounded-workload --modes two")
    if args.recovery_traffic and not args.receipt_probe:
        parser.error("recovery traffic requires a receipt probe")
    for port in (8000, 8080, 5555):
        with socket.socket() as s:
            assert s.connect_ex(("127.0.0.1", port)) != 0, ("occupied port", port)
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for mode in args.modes:
        results.append(run((args.output / mode).resolve(), mode, args.bounded_workload, args.receipt_probe, args.recovery_traffic))
        write_json(args.output / "summary.json", results)
    if len(results) > 1:
        reference = json.loads((args.output / args.modes[0] / "responses.json").read_text())
        for mode, result in zip(args.modes[1:], results[1:]):
            other = json.loads((args.output / mode / "responses.json").read_text())
            assert [(r["name"], r["payload"], r["response"]["choices"][0]["text"]) for r in reference] == [
                (r["name"], r["payload"], r["response"]["choices"][0]["text"]) for r in other]
            if "two" not in args.modes:
                assert result["handoff"]["store_signatures"] == results[0]["handoff"]["store_signatures"], "Default STORE ranges differ"
        write_json(args.output / "comparison.json", dict(passed=True, modes=args.modes,
            same_payloads_and_outputs=True, same_default_store_ranges="not_required" if "two" in args.modes else True,
            performance_experiment=False))


if __name__ == "__main__":
    main()
