"""Offline integrity and cross-cell mechanism audit; never a performance gate."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(1, str(ROOT / "scripts"))

from analyze_whole_step_identity import analyze_cell, read_rows, reuse_gaps, successful_transfers
from audit_bounded_protection import audit_rows as protection_audit
from audit_generation_pilot import audit_rows as generation_audit
from audit_real_structure import audit as input_audit
from audit_replay_measurements import audit_cell as timing_audit
from run_arrival_matrix import digest
from run_fixed_baselines import KV_BYTES, read, token_gate
from run_generation_matrix import generation_closed
from run_real_structure_pilot import TRACE_SHA
from run_reference_baselines import resource_gate
from run_whole_step_identity_diagnostic import LABELS, MODES, configuration
import run_protection_ablation as ablation


def audit_one(cell, label, signature, trace, targets):
    result, manifest = read(cell / "result.json"), read(cell / "run-manifest.json")
    snapshot = read(cell / "generation-summary.json")
    handoffs = read_rows(cell / "handoffs", "*.jsonl")
    service = read_rows(cell / "owned-events", "*.jsonl")
    summary = ablation.merge_protection_summary(cell / "protection-summary")
    generation = generation_audit(handoffs, service, result)
    diagnosis = analyze_cell(cell, label, targets)
    protected = [r for r in handoffs if r.get("event") == "worker_bound" and r.get("source") == "protection"]
    actual = [r["batch_id"] for r in protected if successful_transfers(service, r, "store")]
    pin = protection_audit(handoffs, 32) if protected else None
    logs = ablation.service_error_report(cell, read(cell / "shutdown-context.json"))
    preemptions = result["vllm_delta"].get("vllm:num_preemptions_total")
    expected = len(trace["events"]) + 1 + int(preemptions) if preemptions is not None else -1
    transfers = {phase: Counter((r["request_id"], r["sequence"], r["kind"])
                 for r in service if r.get("event") == phase)
                 for phase in ("transfer_submitted", "terminal_seen", "transfer_retired")}
    checks = dict(
        result=result["passed"], tokens=token_gate(cell, trace), raw_timing=timing_audit(cell)["passed"],
        resources=resource_gate("lazy", result), generation=generation["passed"] and generation_closed(snapshot),
        generation_count=len(snapshot["generations"]) == expected,
        unique_raw_requests=len({g["request_id"] for g in snapshot["generations"]}) == len(trace["events"]) + 1,
        config=read(cell / "config.json") == signature["configs"][label] == configuration(label),
        source=manifest["source_sha256"] == signature["source_sha256"],
        trace=digest(trace) == digest(read(cell / "trace.json")) == manifest["trace_sha256"] == signature["trace_sha256"] == TRACE_SHA,
        budget=manifest["kv_cache_bytes"] == signature["kv_cache_bytes"] == KV_BYTES,
        identity=manifest["mode"] == result["mode"] == MODES[label] and manifest["repeat"] == result["repeat"] == 0,
        healthy=snapshot.get("protection_poisoned") is None and not snapshot.get("fault_error") and not snapshot.get("shutdown_error"),
        receipts=summary["committed_batches"] == summary["receipts"] == len(actual) and summary["failed_receipts"] == 0,
        coverage=(label == "zero" and not protected and summary["committed_batches"] == 0) or
                 (label != "zero" and bool(actual) and pin is not None and pin["passed"]),
        bounded=summary["peak_pinned_blocks"] <= (0 if label == "zero" else 32),
        protection_closed=summary["shutdown_closed"], h2d=result["h2d_bytes"] > 0,
        transfer_conservation=bool(transfers["transfer_submitted"]) and all(
            c == transfers["transfer_submitted"] for c in transfers.values()) and all(
                count == 1 for count in transfers["transfer_submitted"].values()) and all(
                    r.get("succeeded") is True for r in service if r.get("event") == "transfer_retired"),
        fatal_logs=not logs["fatal_errors"], exact_trace_identity=diagnosis["passed"],
        stable_target_lookups=all(not g.get("lookup_state_changed", True)
            for r in diagnosis["routes"] for g in r["generations"]))
    return dict(label=label, passed=all(checks.values()), checks=checks,
        requests=result["requests"], output_tokens=result["output_tokens"],
        committed=summary["committed_batches"], peak_pinned_blocks=summary["peak_pinned_blocks"],
        generation_count=len(snapshot["generations"]), preemptions=preemptions,
        generation_audit=generation, pin_audit=pin, diagnosis=diagnosis,
        lookup_gaps=reuse_gaps(trace, handoffs, service),
        shutdown_teardown_errors=logs["shutdown_teardown_errors"],
        output_hashes={str(r["event_id"]): r["output_sha256"] for r in read(cell / "requests.json")["results"]})


def audit(directory):
    directory = Path(directory)
    plan, trace = read(directory / "plan.json"), read(directory / "trace.json")
    signature = plan["signature"]
    if (plan["stage"] != "identity-diagnostic" or plan["labels"] != list(LABELS)
            or signature["configs"] != {label: configuration(label) for label in LABELS}
            or signature["stable_id_scheme"] != "cachepilot-trace-{event_id:04d}"
            or signature["request_id_randomization_disabled"] is not True or digest(trace) != TRACE_SHA):
        raise ValueError("Identity diagnosis plan differs")
    with tarfile.open(directory / "frozen-source.tar.gz") as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        frozen = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest() for m in members}
    if len(members) != len(frozen) or frozen != signature["source_sha256"]:
        raise ValueError("Frozen source mismatch")
    provenance = input_audit(directory / "input/built", directory / "input/source", directory / "input/catalogue.json")
    if provenance != plan["provenance"] or read(directory / "input/built/trace.json") != trace:
        raise ValueError("Input provenance mismatch")
    targets = []
    for label in LABELS[1:]:
        targets.extend(analyze_cell(directory / f"{label}-r0", label)["logical_targets"])
    rows = [audit_one(directory / f"{label}-r0", label, signature, trace, targets) for label in LABELS]
    target_keys = [set((r["event_id"], r["end"], r["prefix_sha256"])
                       for r in row["diagnosis"]["protected_store_batches"]) for row in rows[1:]]
    same_targets = bool(target_keys[0]) and target_keys[0] == target_keys[1]
    differences = [dict(label=row["label"], event_ids=[k for k, v in rows[0]["output_hashes"].items()
                    if row["output_hashes"].get(k) != v]) for row in rows[1:]]
    return dict(complete=all(r["passed"] for r in rows) and same_targets, same_protected_targets=same_targets,
        passed_cells=sum(r["passed"] for r in rows), rows=rows, output_differences=differences,
        formal_eligible=False, scope="Supplemental offline audit; original source/results preserved. No performance conclusion.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.directory)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("complete", "passed_cells", "same_protected_targets")}))
    raise SystemExit(0 if report["complete"] else 1)
