"""Post-run descriptive analysis; no runtime policy or future-trace decisions.

python analyze_native_deadline.py MATRIX --audit AUDIT.json --output OUT.json
Run the independent matrix audit first. This supplements that audit; it does
not replace resource/generation admission or establish transfer causality.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def metric(text, name, **labels):
    return sum(float(line.rsplit(" ", 1)[1]) for line in text.splitlines()
        if (line.startswith(name + "{") or line.startswith(name + " "))
        and all(f'{key}="{value}"' in line for key, value in labels.items()))


def percentile(rows, key, probability):
    ordered = sorted(rows, key=lambda r: (r[key], r["event_id"]))
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    weight = position - lower
    return dict(value=ordered[lower][key] * (1 - weight) + ordered[upper][key] * weight,
        lower_event=ordered[lower]["event_id"], upper_event=ordered[upper]["event_id"],
        lower_turn=ordered[lower]["turn"], upper_turn=ordered[upper]["turn"],
        upper_weight=weight)


def analyze(root, audit_path):
    report, plan = read(audit_path), read(root / "plan.json")
    if (not report["complete"] or report["passed_cells"] != 6
            or plan["stage"] != "formal" or plan["repeats"] != 3
            or plan["signature"]["comparison"] != "native-deadline"):
        raise ValueError("Complete six-cell native-deadline formal required")
    cells, paired, inputs = [], [], [audit_path, root / "plan.json"]
    required = ("event_id", "session_id", "turn", "arrival_ms", "scheduled_ms",
                "prompt_sha256", "prompt_tokens", "max_tokens")
    for repeat in range(3):
        matched = {}
        for label in ("zero", "native-deadline"):
            directory = root / f"{label}-r{repeat}"
            raw, result = read(directory / "requests.json"), read(directory / "result.json")
            inputs.extend((directory / "requests.json", directory / "result.json"))
            rows = raw["results"]
            if (len(rows) != 60 or len({r["event_id"] for r in rows}) != 60
                    or any(r["error"] is not None or r["http_status"] != 200
                           or r["usage"]["completion_tokens"] != 48 for r in rows)):
                raise ValueError("Incomplete request evidence")
            p95 = percentile(rows, "ttft_ms", 0.95)
            if not math.isclose(p95["value"], result["ttft_ms"]["p95"], abs_tol=1e-6):
                raise ValueError("P95 reconstruction differs")
            durations, transfers = {}, []
            delta = result["vllm_delta"]
            for phase in ("queue", "prefill", "decode", "inference"):
                prefix = f"vllm:request_{phase}_time_seconds"
                if delta[prefix + "_count"] != 60:
                    raise ValueError("Request-duration count differs")
                durations[phase + "_mean_ms"] = delta[prefix + "_sum"] / 60 * 1000
            for direction in ("h2d", "d2h"):
                for phase in ("staging", "kernel"):
                    labels = dict(direction=direction, phase=phase)
                    values = {}
                    for key, name in (("bytes", "lmcache_mp_transfer_phase_bytes_total"),
                            ("elapsed_ms", "lmcache_mp_transfer_phase_elapsed_seconds_total")):
                        values[key] = (metric(raw["lmcache_after"], name, **labels)
                            - metric(raw["lmcache_before"], name, **labels))
                    values["elapsed_ms"] *= 1000
                    if phase == "staging" and not math.isclose(
                            values["bytes"], result[direction + "_bytes"], abs_tol=1):
                        raise ValueError("Transfer byte reconstruction differs")
                    transfers.append(dict(direction=direction, phase=phase, **values))
            cells.append(dict(label=label, repeat=repeat,
                ttft_p95=p95, ttft_mean_ms=statistics.mean(r["ttft_ms"] for r in rows),
                cold_ttft_p95_ms=percentile([r for r in rows if r["turn"] == 0], "ttft_ms", .95)["value"],
                reuse_ttft_p95_ms=percentile([r for r in rows if r["turn"] > 0], "ttft_ms", .95)["value"],
                e2e_p95_ms=percentile(rows, "e2e_ms", .95)["value"],
                client_queue_mean_ms=statistics.mean(r["client_queue_ms"] for r in rows),
                server_request_durations=durations, transfers=transfers,
                slowest_five=[{k:r[k] for k in ("event_id", "session_id", "turn", "ttft_ms")}
                    for r in sorted(rows, key=lambda r:r["ttft_ms"], reverse=True)[:5]]))
            matched[label] = {r["event_id"]: r for r in rows}
        if matched["zero"].keys() != matched["native-deadline"].keys():
            raise ValueError("Event sets differ")
        for event_id, base in sorted(matched["zero"].items()):
            candidate = matched["native-deadline"][event_id]
            if any(base[k] != candidate[k] for k in required):
                raise ValueError("Paired workload differs")
            paired.append(dict(repeat=repeat, event_id=event_id, session_id=base["session_id"],
                turn=base["turn"], zero_ttft_ms=base["ttft_ms"],
                deadline_ttft_ms=candidate["ttft_ms"],
                ttft_change_ms=candidate["ttft_ms"] - base["ttft_ms"],
                output_changed=base["output_sha256"] != candidate["output_sha256"]))
    events = []
    for event_id in sorted({r["event_id"] for r in paired}):
        rows = [r for r in paired if r["event_id"] == event_id]
        changes = [r["ttft_change_ms"] for r in rows]
        events.append(dict(event_id=event_id, session_id=rows[0]["session_id"], turn=rows[0]["turn"],
            individual_change_ms=changes, mean_change_ms=statistics.mean(changes),
            all_faster=all(x < 0 for x in changes), all_slower=all(x > 0 for x in changes)))
    means = {}
    for label in ("zero", "native-deadline"):
        selected = [c for c in cells if c["label"] == label]
        means[label] = {key:statistics.mean(c[key] for c in selected) for key in
            ("ttft_mean_ms", "cold_ttft_p95_ms", "reuse_ttft_p95_ms", "e2e_p95_ms", "client_queue_mean_ms")}
        means[label]["server_request_durations"] = {key:statistics.mean(
            c["server_request_durations"][key] for c in selected)
            for key in selected[0]["server_request_durations"]}
    return dict(cells=cells, descriptive_means=means, paired_requests=paired, per_event=events,
        source_sha256={str(p.relative_to(root)) if p.is_relative_to(root) else p.name:
            hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        scope="Post-hoc descriptive analysis of one fixed workload, three repeats. Event IDs are offline only. "
              "No per-request prefill/transfer attribution; cumulative overlapping request durations and "
              "CUDA stream intervals are not additive TTFT decomposition or evidence of causal contention. "
              "Cold/reuse is defined by turn, not observed GPU/CPU route. Pilot excluded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(analyze(args.matrix, args.audit), indent=2) + "\n", encoding="utf-8")
