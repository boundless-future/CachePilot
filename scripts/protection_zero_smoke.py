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


def run(out, mode, bounded_workload=False, receipt_probe=None, recovery_traffic=False, shared_cancel=False,
        protected_preemption=False, receipt_identity=False, shared_preemption=False, one_chunk=False,
        inflight_budget=False, capacity_fallback=False, kv_cache_bytes=2147483648,
        reuse_id=False, reuse_old_failure=False):
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
        CACHEPILOT_PROBE_DIR=str(probe), KV_CACHE_BYTES=str(kv_cache_bytes))
    os.environ.pop("VLLM_DISABLE_REQUEST_ID_RANDOMIZATION", None)
    if reuse_id:
        os.environ["VLLM_DISABLE_REQUEST_ID_RANDOMIZATION"] = "1"
        os.environ["CACHEPILOT_SHARED_CANCEL_GATE"] = str(out / "release-shared-receipt")
        os.environ["CACHEPILOT_REUSE_OLD_FAILURE"] = "1" if reuse_old_failure else "0"
    config = json.loads((ROOT / "configs/protection-zero.json").read_text())
    config["kv_connector_module_path"] = "scripts.protection_probe_connector"
    config["kv_connector"] = "ObservedEvictionProbeConnector" if mode == "baseline" else "ProtectionProbeConnector"
    budget = {"one": 1, "two": 2}.get(mode, 0)
    config["kv_connector_extra_config"]["cachepilot.protection.enabled"] = mode in ("zero", "one", "two")
    config["kv_connector_extra_config"]["cachepilot.protection.max_chunks"] = budget
    if receipt_probe:
        os.environ["CACHEPILOT_PROTECTED_RECEIPT_PROBE"] = receipt_probe
        config["kv_connector_module_path"] = "scripts.protection_receipt_probe"
        config["kv_connector"] = "ReceiptProbeConnector"
    if shared_cancel or protected_preemption or receipt_identity or shared_preemption or inflight_budget:
        os.environ["CACHEPILOT_SHARED_CANCEL_GATE"] = str(out / "release-shared-receipt")
        config["kv_connector_module_path"] = "scripts.protection_shared_cancel_probe"
        config["kv_connector"] = "SharedCancelProbeConnector"
        if protected_preemption or shared_preemption:
            config["kv_connector_module_path"] = "scripts.protection_preemption_probe"
            config["kv_connector"] = "ProtectedPreemptionConnector"
        if receipt_identity:
            config["kv_connector_module_path"] = "scripts.protection_receipt_identity_probe"
            config["kv_connector"] = "ReceiptIdentityProbeConnector"
    if reuse_id:
        config["kv_connector_module_path"] = "scripts.protection_reuse_probe"
        config["kv_connector"] = "ReuseProbeConnector"
    write_json(out / "config.json", config)
    cache = Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    engine = Service(["bash", str(ROOT / "scripts/serve.sh"), "baseline", "--enforce-eager",
        *(["--no-async-scheduling"] if bounded_workload else []),
        *(["--max-num-batched-tokens", "16384"] if protected_preemption or shared_preemption else []),
        "--kv-transfer-config", json.dumps(config)], out / "vllm.log")
    result = dict(passed=False, mode=mode, eager=True, artificial_hold=False,
        protection_budget=budget, bounded_workload=bounded_workload)
    result["receipt_probe"] = receipt_probe
    result["artificial_hold"] = receipt_probe == "delayed"
    result["recovery_traffic"] = recovery_traffic
    result["shared_cancel"] = shared_cancel
    result["protected_preemption"] = protected_preemption
    result["shared_preemption"] = shared_preemption
    result["receipt_identity"] = receipt_identity
    result["one_chunk_workload"] = one_chunk
    result["inflight_budget"] = inflight_budget
    result["capacity_fallback"] = capacity_fallback
    result["kv_cache_bytes"] = kv_cache_bytes
    result["reuse_id"] = reuse_id
    result["reuse_old_failure"] = reuse_old_failure
    result["artificial_hold"] |= shared_cancel or protected_preemption or receipt_identity or shared_preemption or inflight_budget or reuse_id
    responses = []
    tokenizer = AutoTokenizer.from_pretrained(ROOT / "models/Qwen3-4B")
    source_tokens = 320 if one_chunk else 576
    target = tokenizer.encode(PROMPT)[:source_tokens] if bounded_workload else PROMPT
    cancel_driver = None
    if shared_cancel:
        from protection_shared_cancel_driver import SharedCancelDriver
        cancel_driver = SharedCancelDriver(out, events, API, str(ROOT / "models/Qwen3-4B"),
            tokenizer.encode(" Shared prefix cancellation probe.")[:16])
    if protected_preemption or shared_preemption:
        from protection_preemption_driver import ProtectedPreemptionDriver
        cancel_driver = ProtectedPreemptionDriver(out, events, API, str(ROOT / "models/Qwen3-4B"),
            tokenizer.encode(PROMPT)[:576], shared_victim=shared_preemption)
    if receipt_identity:
        from protection_receipt_identity_driver import ReceiptIdentityDriver
        cancel_driver = ReceiptIdentityDriver(out, events)
    if inflight_budget:
        from protection_inflight_budget_driver import InflightBudgetDriver
        cancel_driver = InflightBudgetDriver(out, events)
    if reuse_id:
        from protection_reuse_driver import ReuseDriver
        cancel_driver = ReuseDriver(out, events, API, str(ROOT / "models/Qwen3-4B"),
            tokenizer.encode(PROMPT)[:576])

    def send(name, prompt, max_tokens=32):
        if bounded_workload and max_tokens != 1:
            max_tokens = 8
        payload = dict(model=str(ROOT / "models/Qwen3-4B"), prompt=prompt,
                       temperature=0, seed=42, max_tokens=max_tokens, ignore_eos=True)
        if reuse_id:
            payload["request_id"] = "reuse-" + name
        response = requests.post(API + "/v1/completions", json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        responses.append(dict(name=name, payload=payload, response=data))
        write_json(out / "responses.json", responses)
        if cancel_driver is not None:
            cancel_driver.maybe_cancel()
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
        if capacity_fallback:
            from protection_capacity_driver import exercise_capacity
            result["capacity_driver"] = exercise_capacity(out, events, API,
                str(ROOT / "models/Qwen3-4B"), tokenizer.encode(PROMPT)[:576])
        if not (shared_cancel or protected_preemption or receipt_identity or shared_preemption
                or inflight_budget or capacity_fallback or reuse_id):
            assert send("hot", target) == cold
        # The shared-cancel workload warms each source only once: two identical
        # warmup requests can later emit separate lazy STOREs of the same KV,
        # which would invalidate the original protected-source provenance.
        # First-token differences create distinct prefixes and real pressure.
        for i in range(12):
            prompt = f"Workload-{i}: " + PROMPT
            send(f"pressure-{i:02d}", tokenizer.encode(prompt)[:source_tokens] if bounded_workload else prompt)
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
        if cancel_driver is not None:
            assert cancel_driver.done, "No shared-prefix cancellation coverage"
        before, _ = snapshot(out, "before-restore")
        restored = send("restored", target)
        after, _ = snapshot(out, "after-restore")
        result["external_hit_tokens"] = counter(after, "vllm:external_prefix_cache_hits_total") - counter(before, "vllm:external_prefix_cache_hits_total")
        result["gpu_hit_tokens"] = counter(after, "vllm:prefix_cache_hits_total") - counter(before, "vllm:prefix_cache_hits_total")
        assert result["external_hit_tokens"] >= (256 if one_chunk else 512 if bounded_workload else 1536) and result["gpu_hit_tokens"] == 0
        assert restored == cold
        assert send("restored-hot", target) == cold
        if bounded_workload:
            # Replay every original pressure prefix in BOTH modes. This makes
            # real protected STORE content eligible for H2D/bitwise validation,
            # even if a different candidate is selected on another run.
            originals = [r for r in responses if r["name"].startswith("pressure-")]
            for row in originals:
                assert send("replay-" + row["name"], row["payload"]["prompt"]) == row["response"]["choices"][0]["text"]
            if reuse_id:
                reused = json.loads((out / "reuse-driver.json").read_text())
                assert send("replay-successor", reused["payload"]["prompt"]) == reused["response"]["choices"][0]["text"]
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
        protection_expected = mode == "two" or (mode == "one" and one_chunk)
        result["handoff"] = audit_handoff(events, allow_protection=protection_expected)
        assert result["handoff"]["failed_batches"] == (1 if receipt_probe == "failed-after-success" or reuse_old_failure else 0)
        if mode not in ("baseline", "one", "two"):
            assert result["handoff"]["bypass_decisions"] > 0
        if mode == "one" and not one_chunk:
            from audit_protection_budget import audit_rows as audit_budget
            rows = [json.loads(line) for p in events.glob("handoff-*.jsonl") for line in p.read_text().splitlines()]
            result["budget_fallback"] = audit_budget(rows)
        if protection_expected:
            from audit_bounded_protection import audit_rows as audit_bounded
            result["protection"] = audit_bounded([json.loads(line) for p in events.glob("handoff-*.jsonl")
                for line in p.read_text().splitlines()], budget=budget * 16)
        kv = [json.loads(line) for p in probe.glob("kv-*.jsonl") for line in p.read_text().splitlines()]
        from analyze_kv_probe import summarize_probe
        result["kv"] = summarize_probe(kv)
        assert result["kv"]["covered_and_equal"]
        if protection_expected:
            handoffs = [json.loads(line) for p in events.glob("handoff-*.jsonl") for line in p.read_text().splitlines()]
            protected = {r["prefix_sha256"] for r in handoffs if r["event"] == "protection_metadata_committed"}
            restored_prefixes = {r["token_prefix_sha256"] for r in kv if r["phase"] == "after_retrieve"}
            result["protected_prefixes_retrieved"] = sorted(protected & restored_prefixes)
            result["protected_prefixes_not_retrieved"] = sorted(protected - restored_prefixes)
            assert result["protected_prefixes_retrieved"], "No protected STORE prefix was actually restored"
            from audit_bounded_kv import audit as audit_sources
            result["strict_protected_sources"] = audit_sources(out)
            if shared_cancel:
                from audit_protection_shared_cancel import audit_rows as audit_cancel
                result["shared_cancel_audit"] = audit_cancel(handoffs)
                shared_sources = [row for row in result["strict_protected_sources"]["strict_sources"]
                    if row["batch_id"] == cancel_driver.target["batch_id"]]
                assert {(row["start"], row["end"]) for row in shared_sources} == {
                    (start, start + 256) for start in range(0, cancel_driver.target["end"], 256)
                }, "Cancelled shared prefix lacks complete protected STORE-to-H2D coverage"
                result["shared_cancel_audit"]["strict_sources"] = shared_sources
            if protected_preemption:
                from audit_protection_preemption import audit as audit_preemption
                result["protected_preemption_audit"] = audit_preemption(out)
            if shared_preemption:
                from audit_protection_shared_preemption import audit as audit_shared_preemption
                result["shared_preemption_audit"] = audit_shared_preemption(out)
            if receipt_identity:
                from audit_protection_receipt_identity import audit_rows as audit_identity
                result["receipt_identity_audit"] = audit_identity(handoffs)
                identity_sources = [r for r in result["strict_protected_sources"]["strict_sources"]
                    if r["batch_id"] == cancel_driver.target["batch_id"]]
                assert {(r["start"], r["end"]) for r in identity_sources} == {(0, 256), (256, 512)}
                result["receipt_identity_audit"]["strict_sources"] = identity_sources
            if inflight_budget:
                from audit_protection_inflight_budget import audit as audit_inflight_budget
                result["inflight_budget_audit"] = audit_inflight_budget(out)
            if capacity_fallback:
                from audit_protection_capacity import audit as audit_capacity
                result["capacity_audit"] = audit_capacity(out)
            if receipt_probe:
                from audit_protected_receipt import audit_rows as audit_receipt
                result["receipt_probe_audit"] = audit_receipt(handoffs, receipt_probe)
            if reuse_id:
                from audit_protection_reuse import audit as audit_reuse
                result["reuse_audit"] = audit_reuse(out)
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
    parser.add_argument("--shared-cancel", action="store_true")
    parser.add_argument("--protected-preemption", action="store_true")
    parser.add_argument("--shared-preemption", action="store_true")
    parser.add_argument("--receipt-identity", action="store_true")
    parser.add_argument("--one-chunk", action="store_true")
    parser.add_argument("--inflight-budget", action="store_true")
    parser.add_argument("--capacity-fallback", action="store_true")
    parser.add_argument("--reuse-id", action="store_true")
    parser.add_argument("--reuse-old-failure", action="store_true")
    parser.add_argument("--kv-cache-bytes", type=int, default=2147483648)
    parser.add_argument("--modes", nargs="+", choices=("baseline", "disabled", "zero", "one", "two"),
                        default=["baseline", "disabled", "zero"])
    args = parser.parse_args()
    if {"one", "two"}.intersection(args.modes) and not args.bounded_workload:
        parser.error("nonzero-budget modes require --bounded-workload")
    if args.receipt_probe and (args.modes != ["two"] or not args.bounded_workload):
        parser.error("receipt probes require --bounded-workload --modes two")
    if args.recovery_traffic and not args.receipt_probe:
        parser.error("recovery traffic requires a receipt probe")
    if args.shared_cancel and (args.modes != ["two"] or not args.bounded_workload or args.receipt_probe):
        parser.error("shared cancellation requires --bounded-workload --modes two without --receipt-probe")
    if args.protected_preemption and (args.modes != ["two"] or not args.bounded_workload
            or args.receipt_probe or args.shared_cancel):
        parser.error("protected preemption requires bounded two mode without other probes")
    if args.receipt_identity and (args.modes != ["two"] or not args.bounded_workload
            or args.receipt_probe or args.shared_cancel or args.protected_preemption or args.shared_preemption):
        parser.error("receipt identity requires bounded two mode without other probes")
    if args.shared_preemption and (args.modes != ["two"] or not args.bounded_workload
            or args.receipt_probe or args.shared_cancel or args.protected_preemption):
        parser.error("shared preemption requires bounded two mode without other probes")
    if args.one_chunk and (not args.bounded_workload or set(args.modes) - {"zero", "one"}
            or args.shared_cancel or args.shared_preemption or args.protected_preemption
            or args.receipt_probe or args.receipt_identity or args.inflight_budget
            or args.capacity_fallback):
        parser.error("one-chunk requires bounded zero/one modes without other probes")
    if args.inflight_budget and (args.modes != ["two"] or not args.bounded_workload
            or args.receipt_probe or args.shared_cancel or args.protected_preemption
            or args.receipt_identity or args.shared_preemption):
        parser.error("inflight budget requires bounded two mode without other probes")
    if args.capacity_fallback and (args.modes != ["two"] or not args.bounded_workload
            or args.receipt_probe or args.shared_cancel or args.protected_preemption
            or args.receipt_identity or args.shared_preemption or args.inflight_budget
            or args.one_chunk):
        parser.error("capacity fallback requires bounded two mode without other probes")
    if args.reuse_id and (args.modes != ["two"] or not args.bounded_workload
            or args.receipt_probe or args.shared_cancel or args.protected_preemption
            or args.receipt_identity or args.shared_preemption or args.inflight_budget
            or args.capacity_fallback or args.one_chunk):
        parser.error("raw ID reuse requires bounded two mode without other probes")
    if args.reuse_old_failure and not args.reuse_id:
        parser.error("reuse old failure requires raw ID reuse")
    if args.kv_cache_bytes != 2147483648 and not args.capacity_fallback:
        parser.error("custom KV capacity is only supported by capacity fallback")
    for port in (8000, 8080, 5555):
        with socket.socket() as s:
            assert s.connect_ex(("127.0.0.1", port)) != 0, ("occupied port", port)
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for mode in args.modes:
        results.append(run((args.output / mode).resolve(), mode, args.bounded_workload, args.receipt_probe,
            args.recovery_traffic, args.shared_cancel, args.protected_preemption, args.receipt_identity,
            args.shared_preemption, args.one_chunk, args.inflight_budget,
            args.capacity_fallback, args.kv_cache_bytes, args.reuse_id,
            args.reuse_old_failure))
        write_json(args.output / "summary.json", results)
    if len(results) > 1:
        reference = json.loads((args.output / args.modes[0] / "responses.json").read_text())
        for mode, result in zip(args.modes[1:], results[1:]):
            other = json.loads((args.output / mode / "responses.json").read_text())
            assert [(r["name"], r["payload"], r["response"]["choices"][0]["text"]) for r in reference] == [
                (r["name"], r["payload"], r["response"]["choices"][0]["text"]) for r in other]
            if "two" not in args.modes and not args.one_chunk:
                assert result["handoff"]["store_signatures"] == results[0]["handoff"]["store_signatures"], "Default STORE ranges differ"
        write_json(args.output / "comparison.json", dict(passed=True, modes=args.modes,
            same_payloads_and_outputs=True,
            same_default_store_ranges="not_required" if "two" in args.modes or args.one_chunk else True,
            performance_experiment=False))


if __name__ == "__main__":
    main()
