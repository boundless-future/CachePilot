"""Independently audit frozen policy matrices and preserve incomplete cells.

Performance aggregates are emitted only for the complete planned matrix.
Per-cell observations and failures remain available when the matrix is partial.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def matrix_layout(plan):
    names = list(plan.get("cases", {name: {} for name in ("zero", "one", "two")}))
    return [(name, repeat) for repeat in range(plan["repeats"]) for name in names]


def collect(directory):
    from run_protection_ablation import cache_drained, service_error_report
    from run_fixed_baselines import configure, strict_idle
    from analyze_replay import percentile
    plan = read(directory / "plan.json")
    signature = plan["signature"]
    layout = matrix_layout(plan)
    rows, grouped = [], {}
    for name, repeat in layout:
        cell = directory / f"{name}-r{repeat}"
        row = dict(cell=cell.name, case=name, repeat=repeat, passed=False)
        try:
            result = read(cell / "result.json")
            data = read(cell / "requests.json")
            trace = read(cell / "trace.json")
            manifest = read(cell / "run-manifest.json")
            config = read(cell / "config.json")
            fixed = "cases" in plan
            validation = read(cell / ("fixed-validation.json" if fixed else "idle-validation.json"))
            summary = result["protection_summary"]
            logs = service_error_report(cell, result.get("shutdown_context"))
            expected = {str(e["event_id"]): e["max_tokens"] for e in trace["events"]}
            requests = data["results"]
            ids = [str(r["event_id"]) for r in requests]
            mode = plan["cases"][name]["mode"] if fixed else name
            token_ok = (len(ids) == len(expected) and len(set(ids)) == len(ids)
                and set(ids) == set(expected) and all(r.get("error") is None and
                    (r.get("usage") or {}).get("completion_tokens") == expected[str(r["event_id"])]
                    for r in requests))
            statuses = [result[k] for k in ("status_before_engine_stop", "status_after_engine_stop")]
            checks = dict(
                result_passed=result["passed"], live_validation=validation["passed"],
                validation_signature=(validation["signature"] == signature),
                source_signature=(manifest["source_sha256"] == signature["source_sha256"]),
                trace_signature=(canonical_hash(trace) == signature["trace_sha256"] ==
                    result["trace_sha256"] == manifest["trace_sha256"] == data["trace_sha256"]),
                kv_capacity=(manifest["kv_cache_bytes"] == signature["kv_cache_bytes"]),
                mode=(mode == result["mode"] == manifest["mode"]),
                tokens=token_ok, resources=all(cache_drained(s) and strict_idle(s) for s in statuses),
                gpu_unregister=(statuses[-1]["registered_gpu_ids"] == []),
                protection_shutdown=summary["shutdown_closed"],
                receipts=(summary["committed_batches"] == summary["receipts"] and
                          summary["failed_receipts"] == 0),
                protection_coverage=(mode == "zero" or summary["committed_batches"] > 0),
                h2d_coverage=result["h2d_bytes"] > 0,
                service_logs=not logs["fatal_errors"],
            )
            extra = config["kv_connector_extra_config"]
            if fixed:
                checks["launch_config"] = (configure(config, plan["cases"][name]) == config)
                checks["validation_case"] = validation["case"] == plan["cases"][name]
            else:
                checks["launch_config"] = (
                    config["kv_connector"] == "IdleFinalizeConnector" and
                    config["kv_connector_module_path"] == "scripts.idle_finalize_connector" and
                    extra["cachepilot.idle_finalize"] is True and
                    extra["cachepilot.idle_finalize_grace_seconds"] == signature["idle_grace_seconds"] and
                    extra["cachepilot.protection.max_chunks"] == {"zero": 0, "one": 1, "two": 2}[mode])
            delta = result["vllm_delta"]
            timings = [t for p in summary.get("raw", []) for t in p.get("timings", {}).values()]
            metrics = dict(
                ttft_p50_ms=result["ttft_ms"]["p50"], ttft_p95_ms=result["ttft_ms"]["p95"],
                ttft_p99_ms=result["ttft_ms"]["p99"],
                reuse_ttft_p95_ms=result["reuse_ttft_ms"]["p95"],
                revisit_ttft_p95_ms=percentile([r["ttft_ms"] for r in requests if r["turn"] == 4], 95),
                ready_to_first_p95_ms=result["ready_to_first_ms"]["p95"],
                ready_to_end_p95_ms=result["ready_to_end_ms"]["p95"],
                session_completion_p95_ms=result["session_completion_ms"]["p95"],
                client_queue_p95_ms=result["client_queue_ms"]["p95"],
                server_queue_mean_ms=1000 * delta["vllm:request_queue_time_seconds_sum"] /
                    delta["vllm:request_queue_time_seconds_count"],
                prefill_tokens=delta.get("vllm:request_prefill_kv_computed_tokens_sum", 0),
                gpu_hit_tokens=delta.get("vllm:prefix_cache_hits_total", 0),
                external_hit_tokens=delta.get("vllm:external_prefix_cache_hits_total", 0),
                h2d_bytes=result["h2d_bytes"], d2h_bytes=result["d2h_bytes"],
                protection_commits=summary["committed_batches"], protection_receipts=summary["receipts"],
                peak_pins=summary["peak_pinned_blocks"], peak_inflight=summary["peak_inflight_blocks"],
                idle_drops=summary["events"].get("idle_pending_dropped", 0),
                protection_cpu_ms=sum(t["cpu_ns"] for t in timings) / 1e6,
                protection_wall_ms=sum(t["wall_ns"] for t in timings) / 1e6,
            )
            row.update(passed=all(checks.values()), checks=checks, metrics=metrics,
                       requests=len(requests), output_hashes={str(r["event_id"]): r["output_sha256"] for r in requests},
                       shutdown_teardown_errors=logs["shutdown_teardown_errors"],
                       service_errors=logs["fatal_errors"])
            if row["passed"]:
                grouped.setdefault(name, []).append(row)
        except (OSError, KeyError, ValueError) as exc:
            row["error"] = repr(exc)
        rows.append(row)
    complete = all(r["passed"] for r in rows)
    aggregates = {}
    if complete:
        for name, group in grouped.items():
            aggregates[name] = {key: dict(
                mean=statistics.mean([r["metrics"][key] for r in group]),
                stdev=statistics.stdev([r["metrics"][key] for r in group]) if len(group) > 1 else None,
                individual=[r["metrics"][key] for r in group]) for key in group[0]["metrics"]}
    reference = "h2p5-d64" if "cases" in plan else "zero"
    comparisons = []
    for repeat in range(plan["repeats"]):
        ref = next((r for r in rows if r["case"] == reference and r["repeat"] == repeat), None)
        if ref and "output_hashes" in ref:
            for row in (r for r in rows if r["repeat"] == repeat and "output_hashes" in r):
                keys = set(ref["output_hashes"]) | set(row["output_hashes"])
                comparisons.append(dict(case=row["case"], repeat=repeat,
                    eligible=ref["passed"] and row["passed"], reference=reference,
                    mismatches=sum(ref["output_hashes"].get(k) != row["output_hashes"].get(k) for k in keys),
                    requests=len(keys)))
    return dict(matrix_complete=complete, expected_cells=len(layout),
                passed_cells=sum(r["passed"] for r in rows), stage=plan["stage"],
                signature=signature, selection=plan.get("selection"),
                rows=rows, aggregates=aggregates, output_comparisons=comparisons,
                note="No aggregate or tuning claim for a partial/failed matrix; pilot is separate. n=3 does not establish significance or deterministic decoding.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("matrix_complete", "expected_cells", "passed_cells")}))
    for row in report["rows"]:
        if not row["passed"]:
            print(row["cell"], row.get("error") or {k: v for k, v in row["checks"].items() if not v})
    if not report["matrix_complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
