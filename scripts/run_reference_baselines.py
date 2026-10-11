"""Frozen whole-configuration comparisons including native/owned-immediate.

Uses the established ablation runner for all lazy-policy cells. Native and
immediate references reuse its client, warmup, metrics, and shutdown ordering;
their resource gates explicitly reflect whether an MP service exists.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

import requests

try:
    from .run_fixed_baselines import (CASES, DEFAULT_CASE, KV_BYTES, configure,
        read, select_fixed, strict_idle, token_gate, validate_completed)
except ImportError:
    from run_fixed_baselines import (CASES, DEFAULT_CASE, KV_BYTES, configure,
        read, select_fixed, strict_idle, token_gate, validate_completed)


def cases_for(selected):
    if selected not in CASES or CASES[selected]["mode"] != "zero":
        raise ValueError("Reference selection must be a fixed baseline")
    cases = {"native": dict(kind="native"), "owned-immediate": dict(kind="immediate")}
    for name in dict.fromkeys((DEFAULT_CASE, selected, "protect-one", "protect-two")):
        cases[name] = dict(kind="lazy", **CASES[name])
    return cases


def immediate_config(original):
    config = json.loads(json.dumps(original))
    if config["kv_connector"] != "OwnedServiceConnector":
        raise ValueError("Expected the established owned-service configuration")
    config["kv_connector_extra_config"]["lmcache.mp.lazy_offload"] = False
    return config


def reference_logs(out, shutdown_context, native):
    import run_protection_ablation as ablation
    if not native:
        return ablation.service_error_report(out, shutdown_context)
    # Native has no LMCache process or log; never manufacture one to satisfy a gate.
    path = out / "vllm.log"
    if not path.exists():
        return dict(fatal_errors=[dict(file="vllm.log", line=0, text="missing service log")],
                    shutdown_teardown_errors=[])
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    known = {i for start, end in ablation._known_shutdown_output_handler_ranges(
        "vllm.log", lines, shutdown_context) for i in range(start, end + 1)}
    fatal, teardown = [], []
    for number, line in enumerate(lines, 1):
        clean = ablation._clean_log_line(line)
        if re.search(r"\bERROR\b|Traceback \(most recent call last\)", clean):
            (teardown if number in known else fatal).append(dict(file="vllm.log", line=number, text=clean))
    return dict(fatal_errors=fatal, shutdown_teardown_errors=teardown)


def resource_gate(kind, result):
    if kind == "native":
        return result.get("engine_returncode") == 0 and result.get("mp_service_started") is False
    import run_protection_ablation as ablation
    states = [result.get(k, {}) for k in ("status_before_engine_stop", "status_after_engine_stop")]
    return (all(ablation.cache_drained(s) and strict_idle(s) for s in states)
            and states[-1].get("registered_gpu_ids") == [])


def run_plain(out, name, repeat, trace):
    import run_protection_ablation as ablation
    from validate_environment import ROOT, Service, write_json
    from replay_trace import replay
    from analyze_replay import summarize
    native = name == "native"
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for key in list(os.environ):
        if key.startswith("CACHEPILOT_"):
            os.environ.pop(key)
    model = str(ROOT / "models/Qwen3-4B")
    os.environ.update(LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080", VLLM_PORT="8000",
        MODEL_PATH=model, KV_CACHE_BYTES=str(KV_BYTES),
        CACHEPILOT_OWNED_SERVICE_DIR=str(out / "owned-events"), CACHEPILOT_OWNED_EVENT_LOG="0")
    os.environ.pop("VLLM_DISABLE_REQUEST_ID_RANDOMIZATION", None)
    config = dict(native=True) if native else immediate_config(read(ROOT / "configs/owned-service.json"))
    trace_sha = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
    write_json(out / "config.json", config)
    write_json(out / "trace.json", trace)
    write_json(out / "run-manifest.json", dict(mode=name, repeat=repeat, model=model,
        kv_cache_bytes=KV_BYTES, trace_sha256=trace_sha, source_sha256=ablation.source_sha()))
    command = ["bash", str(ROOT / "scripts/serve.sh"), "baseline", "--enforce-eager", "--no-async-scheduling"]
    if not native:
        command += ["--kv-transfer-config", json.dumps(config)]
    write_json(out / "launch-command.json", command)
    engine = Service(command, out / "vllm.log")
    cache = None if native else Service(["bash", str(ROOT / "scripts/lmcache-server.sh")], out / "lmcache.log")
    result = dict(mode=name, repeat=repeat, passed=False, mp_service_started=not native)
    try:
        if cache:
            cache.start(ablation.CACHE + "/status")
        engine.start(ablation.API + "/health")
        warmup = requests.post(ablation.API + "/v1/completions", json=dict(model=model,
            prompt="warmup", max_tokens=8, temperature=0, seed=42, ignore_eos=True), timeout=120)
        warmup.raise_for_status()
        write_json(out / "warmup.json", warmup.json())
        before_vllm = ablation.text(ablation.API + "/metrics")
        before_cache = ablation.text(ablation.CACHE + "/metrics") if cache else ""
        data = asyncio.run(replay(trace, ablation.API, model, "open"))
        after_vllm = ablation.text(ablation.API + "/metrics")
        after_cache = ablation.text(ablation.CACHE + "/metrics") if cache else ""
        data.update(vllm_before=before_vllm, vllm_after=after_vllm,
            lmcache_before=before_cache, lmcache_after=after_cache, policy=name,
            repeat=repeat, trace_sha256=trace_sha)
        write_json(out / "requests.json", data)
        result.update(summarize(data), trace_sha256=trace_sha)
        if cache:
            for direction in ("h2d", "d2h"):
                result[direction + "_bytes"] = (ablation.metric(after_cache,
                    "lmcache_mp_transfer_phase_bytes_total", direction=direction, phase="staging") -
                    ablation.metric(before_cache, "lmcache_mp_transfer_phase_bytes_total", direction=direction, phase="staging"))
            result["status_before_engine_stop"] = ablation.wait_drained()
            drained = ablation.cache_drained(result["status_before_engine_stop"])
        else:
            # No transfer/owner resources exist in native vLLM.
            drained = True
        result["shutdown_context"] = dict(requested_unix=time.time(), pid=engine.process.pid,
            process_alive=engine.process.poll() is None, requests_complete=token_gate(out, trace),
            runtime_drained=drained,
            log_lines_before_stop=len((out / "vllm.log").read_text(errors="replace").splitlines()))
        write_json(out / "shutdown-context.json", result["shutdown_context"])
        engine.stop()
        result["engine_returncode"] = engine.process.returncode
        result["shutdown_context"]["returncode"] = engine.process.returncode
        write_json(out / "shutdown-context.json", result["shutdown_context"])
        if cache:
            deadline = time.monotonic() + 30
            while True:
                state = ablation.json_url(ablation.CACHE + "/status")
                result["status_after_engine_stop"] = state
                if not state.get("registered_gpu_ids") or time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
        result["passed"] = (token_gate(out, trace) and result["errors"] == 0 and
            resource_gate("native" if native else "immediate", result) and
            (native or result["h2d_bytes"] > 0) and result["engine_returncode"] == 0)
    except BaseException as exc:
        result["error"] = repr(exc)
        raise
    finally:
        engine.stop()
        if cache:
            cache.stop()
        logs = reference_logs(out, result.get("shutdown_context"), native)
        result.update(service_errors=logs["fatal_errors"], shutdown_teardown_errors=logs["shutdown_teardown_errors"])
        result["passed"] &= not result["service_errors"]
        write_json(out / "result.json", result)
        if result.get("error") is None and not result["passed"]:
            raise RuntimeError(f"Reference cell failed: {out.name}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=("pilot", "formal"))
    parser.add_argument("--tuning", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--stop-starting-at", required=True)
    args = parser.parse_args()
    if (args.stage == "formal") != (args.pilot is not None):
        parser.error("Only formal requires --pilot")
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None:
        parser.error("Cutoff requires a timezone")
    import run_protection_ablation as ablation
    from validate_environment import Service, write_json
    tuning_plan = read(args.tuning / "plan.json")
    selection = select_fixed(validate_completed(args.tuning, tuning_plan["signature"], "formal", CASES, 3))
    sources = ablation.source_sha()
    if any(sources.get(k) != v for k, v in tuning_plan["signature"]["source_sha256"].items()):
        raise ValueError("An existing source changed since fixed-parameter tuning")
    cases = cases_for(selection["selected"])
    trace = read(args.trace)
    ablation.validate_trace(trace)
    signature = dict(source_sha256=sources, kv_cache_bytes=KV_BYTES,
        trace_sha256=hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest(),
        cases=cases, gate="references-v1")
    if args.pilot:
        prior = read(args.pilot / "plan.json")
        if prior["signature"] != signature or prior["stage"] != "pilot" or prior["repeats"] != 1:
            raise ValueError("Pilot signature mismatch")
        for name in cases:
            validation = read(args.pilot / f"{name}-r0/reference-validation.json")
            if not (validation["passed"] and validation["signature"] == signature and
                    read(args.pilot / f"{name}-r0/result.json")["passed"]):
                raise ValueError("Pilot cell did not pass")
    ablation.ports_free()
    args.output.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.stage == "pilot" else 3
    write_json(args.output / "trace.json", trace)
    write_json(args.output / "plan.json", dict(stage=args.stage, signature=signature, cases=cases,
        repeats=repeats, selection=selection, cutoff=args.stop_starting_at,
        tuning=str(args.tuning), pilot=str(args.pilot) if args.pilot else None))
    current = None
    class ReferenceService(Service):
        def start(self, health, timeout=900):
            if self.log.name == "vllm.log":
                i = self.command.index("--kv-transfer-config") + 1
                config = configure(json.loads(self.command[i]), current)
                self.command[i] = json.dumps(config)
                write_json(self.log.parent / "config.json", config)
            return super().start(health, timeout)
    ablation.Service = ReferenceService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda s: original_drained(s) and strict_idle(s)
    completed = []
    for repeat in range(repeats):
        names = list(cases)
        shift = repeat * 3 % len(names)
        for name in names[shift:] + names[:shift]:
            if datetime.now(timezone.utc) >= cutoff:
                write_json(args.output / "stopped-at-cutoff.json", dict(next_cell=f"{name}-r{repeat}", completed=len(completed)))
                return
            if ablation.source_sha() != sources:
                raise RuntimeError("Sources changed during frozen comparison")
            ablation.ports_free()
            current = cases[name]
            cell = args.output / f"{name}-r{repeat}"
            print(f"START {cell.name}", flush=True)
            if current["kind"] == "lazy":
                result = ablation.run_cell(cell, current["mode"], repeat, trace,
                    str(ablation.ROOT / "models/Qwen3-4B"), KV_BYTES)
            else:
                result = run_plain(cell, name, repeat, trace)
            checks = dict(result_passed=result["passed"], tokens=token_gate(cell, trace),
                resources=resource_gate(current["kind"], result))
            validation = dict(passed=all(checks.values()), checks=checks, signature=signature, case=current)
            write_json(cell / "reference-validation.json", validation)
            if not validation["passed"]:
                raise RuntimeError(f"Reference validation failed: {cell.name}")
            completed.append(dict(cell=cell.name, passed=True))
            write_json(args.output / "completed.json", completed)
            print(f"DONE {cell.name}", flush=True)


if __name__ == "__main__":
    main()
