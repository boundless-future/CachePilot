"""Frozen workload follow-ups; reuse established reference measurements."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


BASE_TRACE_SHA = "015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def compress_arrivals(base):
    """Change only planned open-loop arrival times; never change prompt or decode."""
    if digest(base) != BASE_TRACE_SHA:
        raise ValueError("Expected preregistered reference trace")
    trace = copy.deepcopy(base)
    trace["trace_id"] += "-arrivals-half"
    for event in trace["events"]:
        event["arrival_ms"] *= 0.5
    return trace


def extend_prefill(base, tokenizer):
    """Prepend fixed session-specific evidence; preserve arrivals and decode budgets."""
    if digest(base) != BASE_TRACE_SHA:
        raise ValueError("Expected preregistered reference trace")
    trace = copy.deepcopy(base)
    trace["trace_id"] += "-prefill-plus3072"
    trace["workload_variant"] = "long-prefill"
    prefixes = {}
    for event in trace["events"]:
        session = event["session_id"]
        if session not in prefixes:
            text = (f"Long context evidence for {session}.\n" + "\n".join(
                f"Record {i}: inspect deterministic repository evidence for session {session}."
                for i in range(1024)))
            ids = tokenizer.encode(text, add_special_tokens=False)[:3072]
            if len(ids) != 3072:
                raise ValueError("Insufficient fixed prefix material")
            prefixes[session] = tokenizer.decode(ids) + "\n"
        event["prompt"] = prefixes[session] + event["prompt"]
        event["prompt_sha256"] = hashlib.sha256(event["prompt"].encode()).hexdigest()
        event["prompt_tokens"] = len(tokenizer.encode(event["prompt"], add_special_tokens=False))
        if event["prompt_tokens"] + event["max_tokens"] > 8192:
            raise ValueError("Extended prefill exceeds unchanged context limit")
    return trace


def validate_pilot(directory, signature, cases):
    from summarize_reference_matrix import collect
    plan = json.loads((directory / "plan.json").read_text())
    if (plan["stage"] != "pilot" or plan["repeats"] != 1
            or plan["signature"] != signature or plan["cases"] != cases):
        raise ValueError("Pilot source/trace/config signature differs")
    if not collect(directory)["matrix_complete"]:
        raise ValueError("Pilot independent audit did not pass")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("pilot", "formal"), required=True)
    parser.add_argument("--workload", choices=("compressed-arrivals", "long-prefill"),
                        default="compressed-arrivals")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--stop-starting-at", required=True)
    args = parser.parse_args()
    if (args.stage == "formal") != (args.pilot is not None):
        parser.error("Only formal requires a pilot")
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None:
        parser.error("Cutoff requires a timezone")
    import run_protection_ablation as ablation
    from run_fixed_baselines import configure, KV_BYTES, read, strict_idle, token_gate
    from run_reference_baselines import run_plain, resource_gate, cases_for
    from summarize_reference_matrix import collect
    from validate_environment import Service, write_json

    prior = read(args.reference / "plan.json")
    if (prior["stage"] != "formal" or prior["scope"] != "unprotected"
            or prior["repeats"] != 3 or not collect(args.reference)["matrix_complete"]):
        raise ValueError("Requires complete independently audited unprotected reference formal")
    sources = ablation.source_sha()
    if any(sources.get(k) != v for k, v in prior["signature"]["source_sha256"].items()):
        raise ValueError("An existing source changed since reference formal")
    selection = prior["selection"]
    cases = cases_for(selection["selected"], "unprotected")
    if prior["cases"] != cases:
        raise ValueError("Reference configuration selection differs")
    base = read(args.reference / "trace.json")
    if args.workload == "long-prefill":
        from transformers import AutoTokenizer
        trace = extend_prefill(base, AutoTokenizer.from_pretrained(
            str(ablation.ROOT / "models/Qwen3-4B"), local_files_only=True))
    else:
        trace = compress_arrivals(base)
    ablation.validate_trace(trace)
    signature = dict(source_sha256=sources, kv_cache_bytes=KV_BYTES,
        trace_sha256=digest(trace), cases=cases, gate=args.workload + "-v1",
        scope=args.workload, base_trace_sha256=BASE_TRACE_SHA,
        intervention=({"prefix_material_tokens": 3072} if args.workload == "long-prefill"
                      else {"arrival_scale": 0.5}))
    if args.pilot:
        validate_pilot(args.pilot, signature, cases)
    ablation.ports_free()
    args.output.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.stage == "pilot" else 3
    write_json(args.output / "trace.json", trace)
    write_json(args.output / "plan.json", dict(stage=args.stage, signature=signature,
        cases=cases, repeats=repeats, selection=selection, scope=args.workload,
        reference=str(args.reference), cutoff=args.stop_starting_at,
        pilot=str(args.pilot) if args.pilot else None))
    current = None

    class ArrivalService(Service):
        def start(self, health, timeout=900):
            if self.log.name == "vllm.log":
                i = self.command.index("--kv-transfer-config") + 1
                config = configure(json.loads(self.command[i]), current)
                self.command[i] = json.dumps(config)
                write_json(self.log.parent / "config.json", config)
            return super().start(health, timeout)

    ablation.Service = ArrivalService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle(state)
    completed = []
    for repeat in range(repeats):
        names = list(cases)
        shift = repeat * 3 % len(names)
        for name in names[shift:] + names[:shift]:
            if datetime.now(timezone.utc) >= cutoff:
                write_json(args.output / "stopped-at-cutoff.json",
                    dict(next_cell=f"{name}-r{repeat}", completed=len(completed)))
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
            validation = dict(passed=all(checks.values()), checks=checks,
                signature=signature, case=current)
            write_json(cell / "reference-validation.json", validation)
            if not validation["passed"]:
                raise RuntimeError(f"Arrival cell failed: {cell.name}")
            completed.append(dict(cell=cell.name, passed=True))
            write_json(args.output / "completed.json", completed)
            print(f"DONE {cell.name}", flush=True)


if __name__ == "__main__":
    main()
