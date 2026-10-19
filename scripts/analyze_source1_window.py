"""Summarize passive source-event 1 pending-window observations."""
import json
from pathlib import Path


def analyze_rows(rows):
    observations = [row for row in rows
        if row.get("event") == "source1_window_snapshot"
        and row.get("request_id") == "cmpl-cachepilot-trace-0001-0"]
    observations.sort(key=lambda row: row["observation"])
    if not observations:
        return dict(passed=False, reason="no_source1_pending_observations",
                    observations=0, first_hash_loss=None, safe_windows=[])
    losses = [row for row in observations
        if row.get("all_pending_hashes_intact") is False]
    windows = [row for row in observations
        if row.get("actionable_current_front") is True]
    return dict(passed=True, observations=len(observations),
        first_hash_loss=None if not losses else dict(
            observation=losses[0]["observation"], stage=losses[0]["stage"],
            pending=losses[0]["pending"]),
        safe_windows=[dict(observation=row["observation"], stage=row["stage"],
            pending=row["pending"], free_blocks=row["free_blocks"],
            reserve_blocks=row["reserve_blocks"],
            max_safe_protection_blocks=row["max_safe_protection_blocks"])
            for row in windows],
        first_pending=observations[0], last_pending=observations[-1],
        conclusion=("safe_candidate_window_observed" if windows else
                    "no_safe_candidate_window_observed"))


def audit(directory):
    root = Path(directory)
    rows = [json.loads(line) for path in sorted((root / "handoffs").glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return analyze_rows(rows)


def source_reuse(trace, handoffs, service):
    """Require original STORE receipts and native success, then inspect event 9.

    Successful history is not CPU residency or KV bitwise correctness. Cache
    matches are observations after Connector return, not lookup issue times.
    """
    from analyze_whole_step_identity import prefix_sha, successful_transfers, _generation_route
    events = {event["event_id"]: event for event in trace["events"]}
    source, event = events[1], events[9]
    end = 4352
    if source["prompt"][:end] != event["prompt"][:end] or len(source["prompt"]) < end:
        raise ValueError("Expected fixed source 1 to event 9 shared prefix")
    source_id, request_id = "cmpl-cachepilot-trace-0001-0", "cmpl-cachepilot-trace-0009-0"
    stores = []
    for op in handoffs:
        if (op.get("event") != "worker_bound" or op.get("role") != "WORKER"
                or op.get("direction") != "STORE" or op.get("request_id") != source_id
                or not 0 <= op["start"] < op["end"] <= len(source["prompt"])
                or op["prefix_sha256"] != prefix_sha(source["prompt"][:op["end"]])):
            continue
        receipts = [row for row in handoffs if row.get("event") == "scheduler_receipt"
            and row.get("role") == "SCHEDULER" and row.get("direction") == "STORE"
            and row.get("request_id") == source_id and row.get("wire_id") == op["wire_id"]
            and row.get("batch_id") == op["batch_id"] and row.get("failed") is False
            and row["monotonic_ns"] >= op["monotonic_ns"]]
        completions = [max(receipt["monotonic_ns"], transfer["monotonic_ns"])
            for receipt in receipts for transfer in successful_transfers(service, op, "store")]
        if completions:
            stores.append(dict(start=op["start"], end=op["end"], batch_id=op["batch_id"],
                wire_id=op["wire_id"], completed_ns=min(completions)))
    cursor, contiguous_completed_ns = 0, None
    for op in sorted(stores, key=lambda row: (row["start"], row["end"])):
        if op["start"] > cursor:
            break
        if op["end"] > cursor:
            cursor = op["end"]
            contiguous_completed_ns = max(contiguous_completed_ns or 0, op["completed_ns"])
        if cursor >= end:
            break
    target = dict(event_id=1, end=end, prefix_sha256=prefix_sha(source["prompt"][:end]))
    generations = sorted((row for row in handoffs if row.get("event") == "generation_registered"
        and row.get("role") == "SCHEDULER" and row.get("request_id") == request_id),
        key=lambda row: row["monotonic_ns"])
    matches = [row for row in handoffs if row.get("event") == "request_cache_match"
               and row.get("role") == "SCHEDULER"]
    operations = [row for row in handoffs if row.get("event") == "scheduler_metadata"
                  and row.get("role") == "SCHEDULER"]
    routes = [_generation_route(target, event, dict(raw_request_id=request_id),
                               generation, matches, operations, service)
              for generation in generations]
    first_ns = min((row["cache_match_ns"] for row in routes
                    if row["cache_match_ns"] is not None), default=None)
    losses = [row["monotonic_ns"] for row in handoffs
        if row.get("event") == "source1_window_snapshot"
        and row.get("all_pending_hashes_intact") is False]
    full = cursor >= end
    return dict(source_event_id=1, event_id=9, target_tokens=end,
        successful_source_store_ranges=stores, contiguous_saved_tokens=min(cursor, end),
        full_prefix_store_completed_ns=contiguous_completed_ns if full else None,
        first_hash_loss_ns=min(losses, default=None),
        full_store_before_observed_hash_loss=(full and (not losses or contiguous_completed_ns < min(losses))),
        full_store_before_first_cache_match=(full and first_ns is not None and contiguous_completed_ns < first_ns),
        generations=routes, cache_path_observed=bool(routes) and all(
            row["route"] not in ("lookup_unobserved", "lookup_unresolved") for row in routes),
        target_cpu_retrieve_confirmed=any(row["completed_retrieve_ops"] > 0 for row in routes),
        scope="Mechanism only; STORE history is not residency, latency benefit or same-source KV equality")


def audit_run(directory):
    """Independently recheck fixed input, frozen source and service closure."""
    import hashlib
    import tarfile
    from collections import Counter
    from analyze_whole_step_identity import analyze_cell, read_rows
    from audit_generation_pilot import audit_rows as generation_audit
    from audit_real_structure import audit as input_audit
    from run_arrival_matrix import digest
    from run_fixed_baselines import KV_BYTES, read, token_gate
    from run_generation_matrix import generation_closed
    from run_real_structure_pilot import TRACE_SHA
    from run_reference_baselines import resource_gate
    from run_whole_step_identity_diagnostic import configuration, NATIVE_DEADLINE_SECONDS
    import run_protection_ablation as ablation
    root = Path(directory)
    plan, trace = read(root / "plan.json"), read(root / "trace.json")
    cell = root / "zero-r0"
    result = read(cell / "result.json")
    signature = plan["signature"]
    expected = configuration("zero")
    expected.update(kv_connector="Source1WindowConnector",
                    kv_connector_module_path="scripts.source1_window_connector")
    deadline = plan["stage"] == "source1-deadline"
    if deadline:
        expected["kv_connector_extra_config"][
            "lmcache.mp.lazy_offload_max_deferral_seconds"] = NATIVE_DEADLINE_SECONDS
    with tarfile.open(root / "frozen-source.tar.gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        frozen = {member.name: hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                  for member in members}
    manifest = read(cell / "run-manifest.json")
    handoffs = read_rows(cell / "handoffs", "*.jsonl")
    service = read_rows(cell / "owned-events", "*.jsonl")
    generation = generation_audit(handoffs, service, result)
    summary = ablation.merge_protection_summary(cell / "protection-summary")
    window = analyze_rows(handoffs)
    reuse = source_reuse(trace, handoffs, service)
    conservation = [Counter((row["request_id"], row["sequence"], row["kind"])
        for row in service if row.get("event") == phase)
        for phase in ("transfer_submitted", "terminal_seen", "transfer_retired")]
    checks = dict(
        plan=plan["stage"] in ("source1-window", "source1-deadline") and plan["labels"] == ["zero"],
        config=signature["configs"] == {"zero": expected} == {"zero": read(cell / "config.json")},
        source=len(frozen) == len(members) and frozen == signature["source_sha256"] == manifest["source_sha256"],
        input=input_audit(root / "input/built", root / "input/source", root / "input/catalogue.json") == plan["provenance"],
        trace=digest(trace) == TRACE_SHA == signature["trace_sha256"] == manifest["trace_sha256"] == digest(read(cell / "trace.json"))
            and trace == read(root / "input/built/trace.json"),
        budget=signature["kv_cache_bytes"] == manifest["kv_cache_bytes"] == KV_BYTES,
        requests=result["passed"] and token_gate(cell, trace),
        resources=resource_gate("lazy", result),
        identity=analyze_cell(cell, "zero")["passed"],
        generation=generation["passed"] and generation_closed(read(cell / "generation-summary.json")),
        zero_protection=summary["committed_batches"] == summary["peak_pinned_blocks"] == 0,
        protection_closed=summary["shutdown_closed"],
        transfers=bool(conservation[0]) and conservation[0] == conservation[1] == conservation[2]
            and all(count == 1 for count in conservation[0].values())
            and all(row.get("succeeded") is True for row in service if row.get("event") == "transfer_retired"),
        logs=not ablation.service_error_report(cell, read(cell / "shutdown-context.json"))["fatal_errors"],
        observations=window["passed"], cache_path= reuse["cache_path_observed"])
    return dict(complete=all(checks.values()), checks=checks, window=window,
        reuse=reuse,
        generation=generation, requests=result["requests"], output_tokens=result["output_tokens"],
        scope="Native deadline mechanism contrast" if deadline else
              "Passive lifecycle diagnosis only; no performance or intervention conclusion")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.directory), indent=2, sort_keys=True))
