"""One observation-only replay of the held-out protection coverage counterexample."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def inspect_events(events):
    rows, errors = [], []
    fallback = [e for e in events if e.get("event") == "protection_fallback"
                and e.get("reason") == "capacity"]
    for event in fallback:
        try:
            required = event["required_blocks"]
            fields = ("required_blocks", "available_blocks", "inflight_blocks", "budget_blocks",
                      "free", "reserve", "acquired_pins")
            if any(type(event[k]) is not int or event[k] < 0 for k in fields):
                raise ValueError("Non-integer or negative block accounting")
            if not (required > 0 and required % 16 == 0 and event["acquired_pins"] == 0
                    and event["free"] < event["reserve"] + required
                    and event["available_blocks"] >= required
                    and event["available_blocks"] + event["inflight_blocks"] == event["budget_blocks"]):
                raise ValueError("Capacity fallback invariant failed")
            later = [e for e in events if e.get("event") == "scheduler_built"
                and e.get("wire_id") == event["wire_id"] and e.get("source") == "default_manager"
                and e.get("monotonic_ns", 0) > event["monotonic_ns"]]
            rows.append(dict(wire_id=event["wire_id"], free=event["free"], reserve=event["reserve"],
                required=required, reserve_shortfall=event["reserve"] + required - event["free"],
                budget_available=event["available_blocks"], inflight=event["inflight_blocks"],
                acquired_pins=event["acquired_pins"], later_default_batches=len(later)))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
    if len({r["wire_id"] for r in rows}) != len(rows):
        errors.append("Duplicate per-wire fallback observation")
    return dict(passed=bool(rows) and not errors, errors=errors, observations=rows,
                scope="First capacity fallback per wire; not every attempt or allocator demand proof")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-starting-at", required=True)
    args = parser.parse_args()
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        parser.error("Requires timezone-aware future start cutoff")
    import run_protection_ablation as ablation
    from run_arrival_matrix import BASE_TRACE_SHA, digest
    from run_fixed_baselines import configure, CASES, KV_BYTES, read, strict_idle
    from run_reference_baselines import resource_gate
    from audit_replay_measurements import audit_cell
    from validate_environment import Service, write_json
    trace = read(args.trace)
    if digest(trace) != BASE_TRACE_SHA:
        raise ValueError("Requires original failed-validation trace")
    output = args.output.resolve()
    ablation.ports_free()

    class ObservedService(Service):
        def start(self, health, timeout=900):
            if self.log.name == "vllm.log":
                os.environ["CACHEPILOT_HANDOFF_DIR"] = str(output / "handoff")
                i = self.command.index("--kv-transfer-config") + 1
                config = configure(json.loads(self.command[i]), CASES["protect-one"])
                self.command[i] = json.dumps(config)
                write_json(self.log.parent / "config.json", config)
            return super().start(health, timeout)

    ablation.Service = ObservedService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle(state)
    caught = None
    try:
        ablation.run_cell(output, "one", 0, trace, str(ablation.ROOT / "models/Qwen3-4B"), KV_BYTES)
    except Exception as exc:
        caught = repr(exc)
    result = read(output / "result.json")
    events = []
    for path in sorted((output / "handoff").glob("*.jsonl")):
        events.extend(json.loads(line) for line in path.read_text().splitlines())
    observation = inspect_events(events)
    summary = result.get("protection_summary", {})
    checks = dict(measurements=audit_cell(output)["passed"], resources=resource_gate("lazy", result),
        h2d=result.get("h2d_bytes", 0) > 0, no_service_errors=not result.get("service_errors", ["missing"]),
        protection_closed=summary.get("shutdown_closed") is True,
        observations_valid=observation["passed"])
    report = dict(diagnostic_checks=checks, diagnostic_passed=all(checks.values()),
        original_performance_gate_passed=result["passed"], caught=caught, observation=observation,
        zero_coverage_reproduced=(summary.get("committed_batches") == 0 and summary.get("receipts") == 0),
        summary=summary, scope="Diagnostic logging; never aggregate its latency into performance matrices")
    write_json(output / "coverage-diagnostic.json", report)
    print(json.dumps({k: report[k] for k in ("diagnostic_passed", "original_performance_gate_passed", "zero_coverage_reproduced")}))
    if not report["diagnostic_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
