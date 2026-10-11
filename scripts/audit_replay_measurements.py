"""Recompute stored latency summaries from raw SSE/client timestamps.

This offline audit does not import the replay runner or its analysis helpers.
It supplements, rather than replaces, source/resource/protection audits.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics


def read(path):
    return Path(path).read_text(encoding="utf-8")


def close(left, right):
    return (isinstance(left, (int, float)) and isinstance(right, (int, float))
            and math.isfinite(left) and math.isfinite(right)
            and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-5))


def describe(values, percentiles=(50, 95, 99), mean=True):
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError("Missing or nonfinite observations")
    quantiles = statistics.quantiles(values, n=100, method="inclusive") if len(values) > 1 else [values[0]] * 99
    result = {f"p{p}": quantiles[p - 1] for p in percentiles}
    if mean:
        result["mean"] = statistics.mean(values)
    return result


def audit_cell(cell):
    trace = json.loads(read(cell / "trace.json"))
    data = json.loads(read(cell / "requests.json"))
    reported = json.loads(read(cell / "result.json"))
    expected = {str(e["event_id"]): e for e in trace["events"]}
    rows = data["results"]
    errors, values, sessions = [], {}, {}
    ids = [str(row["event_id"]) for row in rows]
    if len(expected) != len(trace["events"]) or len(set(ids)) != len(ids) or set(ids) != set(expected):
        errors.append("event_ids")
    if data.get("mode") != "open":
        errors.append("requires_open_loop_trace")
    for row in rows:
        event_id = str(row["event_id"])
        event = expected.get(event_id)
        if event is None:
            continue
        try:
            if row["error"] is not None or row["http_status"] != 200:
                raise ValueError("failed_request")
            chunks = row["chunk_arrivals_ms"]
            scheduled, sent, finished = (row[k] for k in ("scheduled_ms", "sent_ms", "finished_ms"))
            points = [scheduled, sent, *chunks, finished]
            if not chunks or not all(math.isfinite(v) for v in points):
                raise ValueError("missing_or_nonfinite_timestamps")
            if any(a > b + 1e-5 for a, b in zip(points, points[1:])):
                raise ValueError("timestamp_order")
            if not close(scheduled, event["arrival_ms"]):
                raise ValueError("arrival_mismatch")
            for key in ("session_id", "turn", "prompt_sha256", "prompt_tokens", "max_tokens"):
                if row[key] != event[key]:
                    raise ValueError(f"trace_mismatch:{key}")
            if (row["usage"]["completion_tokens"] != event["max_tokens"] or
                    row["usage"]["prompt_tokens"] != event["prompt_tokens"]):
                raise ValueError("token_contract")
            if hashlib.sha256(row["output_text"].encode()).hexdigest() != row["output_sha256"]:
                raise ValueError("output_hash")
            derived = dict(ttft_ms=chunks[0] - sent, ready_to_first_ms=chunks[0] - scheduled,
                           e2e_ms=finished - sent, ready_to_end_ms=finished - scheduled,
                           client_queue_ms=sent - scheduled)
            for key, value in derived.items():
                if not close(row[key], value):
                    errors.append(f"{event_id}:stored_latency:{key}")
                values.setdefault(key, []).append(value)
            values.setdefault("cold_ttft_ms" if row["turn"] == 0 else "reuse_ttft_ms", []).append(derived["ttft_ms"])
            sessions.setdefault(row["session_id"], []).append((scheduled, finished))
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"{event_id}:{exc}")
    recomputed = {}
    try:
        for key in ("ttft_ms", "ready_to_first_ms", "e2e_ms", "ready_to_end_ms", "client_queue_ms"):
            recomputed[key] = describe(values[key])
        for key in ("cold_ttft_ms", "reuse_ttft_ms"):
            recomputed[key] = describe(values[key], (50, 95), mean=False)
        spans = [max(e for _, e in points) - min(s for s, _ in points) for points in sessions.values()]
        recomputed["session_completion_ms"] = describe(spans, (95,))
        for key, metrics in recomputed.items():
            for metric, value in metrics.items():
                if not close(reported[key][metric], value):
                    errors.append(f"summary:{key}:{metric}")
        if reported["requests"] != len(rows) or reported["errors"] != 0:
            errors.append("summary:request_counts")
        if reported["output_tokens"] != sum(e["max_tokens"] for e in trace["events"]):
            errors.append("summary:output_tokens")
    except (KeyError, ValueError, TypeError) as exc:
        errors.append(f"summary_incomplete:{exc}")
    return dict(cell=cell.name, passed=not errors, requests=len(rows), errors=errors,
                recomputed=recomputed)


def audit_matrix(directory):
    plan = json.loads(read(directory / "plan.json"))
    names = list(plan.get("cases", {name: None for name in ("zero", "one", "two")}))
    results = []
    for repeat in range(plan["repeats"]):
        for name in names:
            cell = directory / f"{name}-r{repeat}"
            try:
                results.append(audit_cell(cell))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                results.append(dict(cell=cell.name, passed=False, errors=[repr(exc)]))
    return dict(passed=bool(results) and all(r["passed"] for r in results),
                expected_cells=len(results), passed_cells=sum(r["passed"] for r in results),
                rows=results, tolerance_ms=1e-5,
                scope="Raw timing/token/hash audit; excludes KV bitwise equivalence and DMA timing.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_matrix(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("passed", "expected_cells", "passed_cells")}))
    for row in report["rows"]:
        if not row["passed"]:
            print(row["cell"], row["errors"])
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
