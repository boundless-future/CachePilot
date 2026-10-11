"""Audit whole-configuration references; native CPU-KV checks are inapplicable."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def collect(directory):
    from audit_replay_measurements import audit_cell
    from run_reference_baselines import reference_logs, resource_gate
    from run_fixed_baselines import configure
    plan = read(directory / "plan.json")
    signature = plan["signature"]
    rows = []
    for repeat in range(plan["repeats"]):
        for name, case in plan["cases"].items():
            cell = directory / f"{name}-r{repeat}"
            row = dict(cell=cell.name, case=name, repeat=repeat, passed=False)
            try:
                result = read(cell / "result.json")
                manifest = read(cell / "run-manifest.json")
                validation = read(cell / "reference-validation.json")
                config = read(cell / "config.json")
                trace = read(cell / "trace.json")
                data = read(cell / "requests.json")
                timing = audit_cell(cell)
                kind = case["kind"]
                mode = case["mode"] if kind == "lazy" else name
                logs = reference_logs(cell, result.get("shutdown_context"), kind == "native")
                checks = dict(result_passed=result["passed"], live_validation=validation["passed"],
                    signature=(validation["signature"] == signature and validation["case"] == case),
                    source=(manifest["source_sha256"] == signature["source_sha256"]),
                    trace=(digest(trace) == signature["trace_sha256"] == manifest["trace_sha256"]
                           == data["trace_sha256"] == result["trace_sha256"]),
                    capacity=(manifest["kv_cache_bytes"] == signature["kv_cache_bytes"]),
                    identity=(manifest["mode"] == result["mode"] == mode and
                              manifest["repeat"] == result["repeat"] == repeat),
                    raw_measurements=timing["passed"], resources=resource_gate(kind, result),
                    service_logs=not logs["fatal_errors"])
                inapplicable = []
                if kind == "native":
                    checks["launch_config"] = config == {"native": True}
                    command = read(cell / "launch-command.json")
                    checks["no_connector"] = "--kv-transfer-config" not in command and "baseline" in command
                    inapplicable = ["CPU KV resources", "H2D/D2H", "protection receipts"]
                else:
                    checks["h2d_coverage"] = result["h2d_bytes"] > 0
                    if kind == "immediate":
                        checks["launch_config"] = (
                            config["kv_connector"] == "OwnedServiceConnector" and
                            config["kv_connector_module_path"] == "scripts.owned_service_connector" and
                            config["kv_role"] == "kv_both" and config["kv_load_failure_policy"] == "recompute" and
                            config["kv_connector_extra_config"].get("lmcache.mp.lazy_offload") is False)
                        checks["service_present"] = result["mp_service_started"] is True
                        checks["normal_exit"] = result["engine_returncode"] == 0
                        inapplicable = ["protection receipts"]
                    else:
                        checks["launch_config"] = configure(config, case) == config
                        summary = result["protection_summary"]
                        checks["protection_shutdown"] = summary["shutdown_closed"]
                        checks["receipts"] = (summary["committed_batches"] == summary["receipts"]
                                               and summary["failed_receipts"] == 0)
                        checks["protection_coverage"] = (summary["committed_batches"] == 0 if mode == "zero"
                                                         else summary["committed_batches"] > 0)
                delta = result["vllm_delta"]
                metrics = {f"{key}_p95": result[key]["p95"] for key in (
                    "ttft_ms", "reuse_ttft_ms", "ready_to_first_ms", "ready_to_end_ms",
                    "session_completion_ms", "client_queue_ms")}
                metrics.update(prefill_tokens=delta.get("vllm:request_prefill_kv_computed_tokens_sum", 0),
                    gpu_hit_tokens=delta.get("vllm:prefix_cache_hits_total", 0),
                    external_hit_tokens=delta.get("vllm:external_prefix_cache_hits_total", 0),
                    server_queue_mean_ms=1000 * delta["vllm:request_queue_time_seconds_sum"] /
                        delta["vllm:request_queue_time_seconds_count"])
                if kind != "native":
                    metrics.update(h2d_bytes=result["h2d_bytes"], d2h_bytes=result["d2h_bytes"])
                if kind == "lazy":
                    metrics.update(protection_commits=summary["committed_batches"],
                        peak_pins=summary["peak_pinned_blocks"], peak_inflight=summary["peak_inflight_blocks"],
                        idle_drops=summary["events"].get("idle_pending_dropped", 0))
                row.update(passed=all(checks.values()), checks=checks, metrics=metrics,
                    inapplicable=inapplicable, requests=len(data["results"]), timing_errors=timing["errors"],
                    output_hashes={str(r["event_id"]): r["output_sha256"] for r in data["results"]},
                    shutdown_teardown_errors=logs["shutdown_teardown_errors"], service_errors=logs["fatal_errors"])
            except (OSError, KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
                row["error"] = repr(exc)
            rows.append(row)
    complete = bool(rows) and all(row["passed"] for row in rows)
    aggregates = {}
    if complete:
        for name in plan["cases"]:
            group = [r for r in rows if r["case"] == name]
            aggregates[name] = {key: dict(mean=statistics.mean(values),
                stdev=statistics.stdev(values) if len(values) > 1 else None, individual=values)
                for key in group[0]["metrics"]
                for values in [[r["metrics"][key] for r in group]]}
    comparisons = []
    for repeat in range(plan["repeats"]):
        native = next(r for r in rows if r["case"] == "native" and r["repeat"] == repeat)
        if "output_hashes" not in native:
            continue
        for row in rows:
            if row["repeat"] == repeat and "output_hashes" in row:
                keys = set(native["output_hashes"]) | set(row["output_hashes"])
                comparisons.append(dict(case=row["case"], repeat=repeat, reference="native",
                    eligible=native["passed"] and row["passed"], requests=len(keys),
                    mismatches=sum(native["output_hashes"].get(k) != row["output_hashes"].get(k) for k in keys)))
    return dict(matrix_complete=complete, expected_cells=len(rows), passed_cells=sum(r["passed"] for r in rows),
        stage=plan["stage"], selection=plan["selection"], signature=signature, rows=rows,
        aggregates=aggregates, output_comparisons=comparisons,
        scope="Whole configurations; pilot separate; no partial aggregates or deterministic decode claim.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("matrix_complete", "expected_cells", "passed_cells")}))
    for row in report["rows"]:
        if not row["passed"]:
            print(row["cell"], row.get("error") or {k: v for k, v in row["checks"].items() if not v})
    if not report["matrix_complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
