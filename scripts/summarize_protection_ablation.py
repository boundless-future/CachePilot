"""Summarize isolated protection-ablation cells without rerunning GPU work."""
import argparse
import collections
import json
from pathlib import Path
import statistics

from analyze_replay import percentile
from run_protection_ablation import cache_drained, service_error_report


def mean(values):
    return statistics.mean(values) if values else None


def output_hashes(cell):
    data = json.loads((cell / "requests.json").read_text())
    return {str(row["event_id"]): row["output_sha256"] for row in data["results"]}


def token_contract(cell):
    data = json.loads((cell / "requests.json").read_text())
    rows = data["results"]
    return dict(requests=len(rows), errors=sum(row["error"] is not None for row in rows),
                complete=sum((row.get("usage") or {}).get("completion_tokens", 0) == row["max_tokens"]
                             for row in rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs, grouped, by_repeat = [], collections.defaultdict(list), collections.defaultdict(list)
    for path in sorted(args.directory.glob("*-r*/result.json")):
        cell, result = path.parent, json.loads(path.read_text())
        if "protection_summary" not in result:
            runs.append(dict(cell=cell.name, mode=result["mode"], repeat=result["repeat"],
                passed=False, excluded=True, error=result.get("error", "incomplete cell")))
            continue
        summary = result["protection_summary"]
        contract = token_contract(cell)
        log_report = service_error_report(cell, result.get("shutdown_context"))
        logs = log_report["fatal_errors"]
        drained = (cache_drained(result["status_before_engine_stop"]) and
                   cache_drained(result["status_after_engine_stop"]) and
                   result["status_after_engine_stop"].get("registered_gpu_ids") == [])
        accepted = (result["passed"] and drained and not logs and summary.get("shutdown_closed", False)
                    and result["h2d_bytes"] > 0 and contract["errors"] == 0
                    and contract["requests"] == contract["complete"]
                    and (result["mode"] == "zero" or summary["committed_batches"] > 0))
        data = json.loads((cell / "requests.json").read_text())
        revisit = [r["ttft_ms"] for r in data["results"] if r["turn"] == 4 and not r["error"]]
        timings = [t for process in summary.get("raw", [])
                   for t in process.get("timings", {}).values()]
        delta = result["vllm_delta"]
        row = dict(cell=cell.name, mode=result["mode"], repeat=result["repeat"],
            passed=bool(accepted), excluded=not accepted, errors=result["errors"], service_errors=logs,
            shutdown_teardown_errors=log_report["shutdown_teardown_errors"],
            trace_sha256=result.get("trace_sha256"),
            ttft_p50_ms=result["ttft_ms"]["p50"], ttft_p95_ms=result["ttft_ms"]["p95"],
            reuse_ttft_p95_ms=result["reuse_ttft_ms"]["p95"],
            client_queue_p95_ms=result["client_queue_ms"]["p95"],
            server_queue_mean_ms=1000 * delta["vllm:request_queue_time_seconds_sum"] / delta["vllm:request_queue_time_seconds_count"],
            revisit_ttft_p95_ms=percentile(revisit, 95),
            session_completion_p95_ms=result["session_completion_ms"]["p95"],
            ready_to_first_p95_ms=result["ready_to_first_ms"]["p95"],
            protection_cpu_ms=sum(t["cpu_ns"] for t in timings) / 1e6,
            protection_wall_ms=sum(t["wall_ns"] for t in timings) / 1e6,
            store_count=result["lmcache_delta"].get("lmcache_mp_num_finished_stores_total"),
            prefill_tokens=result["vllm_delta"].get("vllm:request_prefill_kv_computed_tokens_sum", 0),
            external_hit_tokens=result["vllm_delta"].get("vllm:external_prefix_cache_hits_total", 0),
            gpu_hit_tokens=result["vllm_delta"].get("vllm:prefix_cache_hits_total", 0),
            h2d_bytes=result["h2d_bytes"], d2h_bytes=result["d2h_bytes"],
            protection_commits=summary["committed_batches"],
            protection_receipts=summary["receipts"],
            protection_peak_pins=summary["peak_pinned_blocks"],
            protection_peak_inflight=summary["peak_inflight_blocks"],
            fallbacks=summary["fallbacks"], resource_drained=drained,
            token_contract=contract, output_hashes=output_hashes(cell))
        runs.append(row)
        if accepted:
            grouped[row["mode"]].append(row)
        by_repeat[row["repeat"]].append(row)
    keys = ("ttft_p50_ms", "ttft_p95_ms", "reuse_ttft_p95_ms", "client_queue_p95_ms",
            "server_queue_mean_ms", "revisit_ttft_p95_ms", "session_completion_p95_ms",
            "ready_to_first_p95_ms", "protection_cpu_ms", "protection_wall_ms", "store_count",
            "prefill_tokens", "external_hit_tokens", "gpu_hit_tokens", "h2d_bytes", "d2h_bytes",
            "protection_commits", "protection_receipts", "protection_peak_pins",
            "protection_peak_inflight")
    aggregates = []
    for mode, rows in sorted(grouped.items()):
        aggregates.append(dict(mode=mode, repeats=len(rows), passed=all(row["passed"] for row in rows),
            resource_drained=all(row["resource_drained"] for row in rows),
            metrics={key: dict(mean=mean([row[key] for row in rows if row[key] is not None]),
                individual=[row[key] for row in rows],
                stdev=statistics.stdev([row[key] for row in rows]) if len(rows) > 1 and all(row[key] is not None for row in rows) else None) for key in keys}))
    comparisons = []
    for repeat, rows in sorted(by_repeat.items()):
        reference = next((row for row in rows if row["mode"] == "zero"), None)
        if reference:
            for row in rows:
                comparisons.append(dict(repeat=repeat, mode=row["mode"],
                    eligible=row["passed"] and reference["passed"] and row["trace_sha256"] == reference["trace_sha256"],
                    output_hash_mismatches=sum(row["output_hashes"].get(event) != reference["output_hashes"].get(event)
                        for event in set(row["output_hashes"]) | set(reference["output_hashes"])),
                    requests=len(row["output_hashes"]), token_contract=row["token_contract"]))
    output = dict(runs=runs, aggregates=aggregates, comparisons=comparisons,
        note="Only accepted cells enter aggregates. Output differences are reported, not assumed deterministic. Client queue is not server queue; method timing includes counter instrumentation.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(aggregates, indent=2))


if __name__ == "__main__":
    main()
