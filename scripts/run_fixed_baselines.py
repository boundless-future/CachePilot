"""Frozen fixed-policy tuning and held-out comparison on the idle-owned stack.

The existing ablation runner owns measurement, service lifetime and error gates.
Only its launch config is adapted here; installed packages are never modified.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics


FIXED_CASES = {
    f"h{str(h).replace('.', 'p')}-d{d}": dict(horizon=h, drain=d, mode="zero")
    for h in (1.25, 2.5, 5.0) for d in (1, 64)
}
DEFAULT_CASE = "h2p5-d64"
CASES = dict(FIXED_CASES, **{
    "protect-one": dict(horizon=2.5, drain=64, mode="one"),
    "protect-two": dict(horizon=2.5, drain=64, mode="two"),
})
KV_BYTES = 2147483648
GRACE_SECONDS = 15.0


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def strict_idle(state):
    return (state.get("active_sessions") == 0 and
            state.get("storage_manager", {}).get("l1_manager", {}).get("temporary_count") == 0)


def configure(original, case):
    config = copy.deepcopy(original)
    config.update(kv_connector="IdleFinalizeConnector",
                  kv_connector_module_path="scripts.idle_finalize_connector")
    config["kv_connector_extra_config"].update({
        "lmcache.mp.lazy_offload": True,
        "lmcache.mp.lazy_offload_policy": "EVICTION_AWARE",
        "lmcache.mp.lazy_offload_horizon_steps": case["horizon"],
        "lmcache.mp.lazy_offload_max_drain_per_step": case["drain"],
        "lmcache.mp.lazy_offload_max_deferral_seconds": 0.0,
        "cachepilot.protection.enabled": True,
        "cachepilot.protection.max_chunks": {"zero": 0, "one": 1, "two": 2}[case["mode"]],
        "cachepilot.idle_finalize": True,
        "cachepilot.idle_finalize_grace_seconds": GRACE_SECONDS,
    })
    return config


def validate_completed(directory, signature, stage, cases, repeats):
    plan = read(directory / "plan.json")
    if (plan["stage"] != stage or plan["signature"] != signature or
            plan["cases"] != cases or plan["repeats"] != repeats):
        raise ValueError("Prerequisite plan/source/trace mismatch")
    rows = []
    for repeat in range(repeats):
        for name in cases:
            cell = directory / f"{name}-r{repeat}"
            result, validation = read(cell / "result.json"), read(cell / "fixed-validation.json")
            if not (result["passed"] and validation["passed"] and
                    validation["signature"] == signature and validation["case"] == cases[name]):
                raise ValueError(f"Failed or mismatched prerequisite cell: {cell}")
            rows.append((name, repeat, result))
    return rows


def select_fixed(rows):
    scores = {}
    for name in FIXED_CASES:
        values = [r["ttft_ms"]["p95"] for n, _, r in rows if n == name]
        if len(values) != 3 or not all(isinstance(v, (int, float)) and v > 0 for v in values):
            raise ValueError("Selection requires three valid repetitions of every fixed case")
        scores[name] = dict(mean=statistics.mean(values), individual=values,
                           stdev=statistics.stdev(values))
    # Exact score ties use the predeclared lexicographic case order.
    selected = min(scores, key=lambda n: (scores[n]["mean"], n))
    return dict(selected=selected, scores=scores,
                criterion="lowest mean TTFT P95 on development; exact ties lexicographic")


def token_gate(cell, trace):
    rows = read(cell / "requests.json")["results"]
    expected = {str(e["event_id"]): e["max_tokens"] for e in trace["events"]}
    ids = [str(r["event_id"]) for r in rows]
    return (len(ids) == len(expected) and len(set(ids)) == len(ids) and set(ids) == set(expected)
            and all(r.get("error") is None and
                    (r.get("usage") or {}).get("completion_tokens") == expected[str(r["event_id"])]
                    for r in rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("pilot", "formal", "validation"), required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prerequisite", type=Path)
    parser.add_argument("--stop-starting-at", required=True)
    args = parser.parse_args()
    if (args.stage == "pilot") != (args.prerequisite is None):
        parser.error("Formal/validation require a prerequisite; pilot must not have one")
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None:
        parser.error("Cutoff requires a timezone")
    import run_protection_ablation as ablation
    from validate_environment import Service, write_json
    trace = read(args.trace)
    ablation.validate_trace(trace)
    signature = dict(source_sha256=ablation.source_sha(), kv_cache_bytes=KV_BYTES,
                     grace_seconds=GRACE_SECONDS, gate="fixed-baselines-v1",
                     trace_sha256=hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest())
    cases, selection = dict(CASES), None
    if args.stage == "formal":
        validate_completed(args.prerequisite, signature, "pilot", CASES, 1)
    elif args.stage == "validation":
        tuning_signature = read(args.prerequisite / "plan.json")["signature"]
        for key in signature:
            if key != "trace_sha256" and signature[key] != tuning_signature[key]:
                raise ValueError("Validation runtime/source differs from tuning")
        rows = validate_completed(args.prerequisite, tuning_signature, "formal", CASES, 3)
        selection = select_fixed(rows)
        development = read(args.prerequisite / "trace.json")
        if ({e["prompt_sha256"] for e in development["events"]} &
                {e["prompt_sha256"] for e in trace["events"]}):
            raise ValueError("Held-out prompts overlap development")
        cases = {n: CASES[n] for n in dict.fromkeys(
            (DEFAULT_CASE, selection["selected"], "protect-one", "protect-two"))}
    ablation.ports_free()
    args.output.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.stage == "pilot" else 3
    write_json(args.output / "trace.json", trace)
    write_json(args.output / "plan.json", dict(stage=args.stage, signature=signature,
        cases=cases, repeats=repeats, selection=selection, diagnostic_logs=False,
        cutoff=args.stop_starting_at, prerequisite=str(args.prerequisite) if args.prerequisite else None))
    current = None

    class FixedService(Service):
        def start(self, health, timeout=900):
            if self.log.name == "vllm.log":
                index = self.command.index("--kv-transfer-config") + 1
                config = configure(json.loads(self.command[index]), current)
                self.command[index] = json.dumps(config)
                write_json(self.log.parent / "config.json", config)
            return super().start(health, timeout)

    ablation.Service = FixedService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda s: original_drained(s) and strict_idle(s)
    completed = []
    for repeat in range(repeats):
        order = list(cases)
        # Rotate by three to spread both horizon and candidate positions.
        shift = (repeat * 3) % len(order)
        for name in order[shift:] + order[:shift]:
            if datetime.now(timezone.utc) >= cutoff:
                write_json(args.output / "stopped-at-cutoff.json", dict(
                    next_cell=f"{name}-r{repeat}", completed=len(completed)))
                return
            if ablation.source_sha() != signature["source_sha256"]:
                raise RuntimeError("Sources changed during frozen experiment")
            ablation.ports_free()
            current = cases[name]
            cell = args.output / f"{name}-r{repeat}"
            print(f"START {cell.name}", flush=True)
            result = ablation.run_cell(cell, current["mode"], repeat, trace,
                                      str(ablation.ROOT / "models/Qwen3-4B"), KV_BYTES)
            config = read(cell / "config.json")
            checks = dict(result_passed=result["passed"], tokens=token_gate(cell, trace),
                runtime_idle=strict_idle(result["status_before_engine_stop"]),
                shutdown_idle=strict_idle(result["status_after_engine_stop"]),
                launch_config=(configure(config, current) == config))
            validated = dict(passed=all(checks.values()), checks=checks, case=current,
                             signature=signature)
            write_json(cell / "fixed-validation.json", validated)
            if not validated["passed"]:
                raise RuntimeError(f"Fixed baseline gate failed: {cell.name}")
            completed.append(dict(cell=cell.name, passed=True))
            write_json(args.output / "completed.json", completed)
            print(f"DONE {cell.name}", flush=True)


if __name__ == "__main__":
    main()
