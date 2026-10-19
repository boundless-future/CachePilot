"""Join stable IDs, per-generation cache matches and native transfers.

Only logical targets cross cells; clocks/transfers always stay in their cell.
Compute demand is inferred at cache-match observation, not executed work.
Observation timestamps are after Connector return, never lookup issue times.
"""
import hashlib
import json
from pathlib import Path


def read_rows(directory, pattern):
    return [json.loads(line) for path in sorted(Path(directory).glob(pattern))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def prefix_sha(tokens):
    # The frozen anonymous trace has no cache salt.
    return hashlib.sha256(json.dumps(["", list(tokens)], separators=(",", ":")).encode()).hexdigest()


def successful_transfers(service, operation, kind):
    """Require the same wire, physical blocks, sequence and native terminal."""
    completed = []
    for submitted in service:
        if (submitted.get("event") != "transfer_submitted" or submitted.get("kind") != kind
                or submitted.get("request_id") != operation["wire_id"]
                or submitted.get("blocks") != operation["block_ids"]
                or submitted["monotonic_ns"] < operation["monotonic_ns"]):
            continue
        key = (submitted["request_id"], submitted["sequence"], kind)
        terminal = [r for r in service if r.get("event") == "terminal_seen"
                    and (r.get("request_id"), r.get("sequence"), r.get("kind")) == key
                    and r["monotonic_ns"] >= submitted["monotonic_ns"]]
        retired = [r for r in service if r.get("event") == "transfer_retired"
                   and (r.get("request_id"), r.get("sequence"), r.get("kind")) == key
                   and r.get("succeeded") is True and any(
                       r["monotonic_ns"] >= t["monotonic_ns"] for t in terminal)]
        completed.extend(retired)
    return completed


def _store_completion(service, handoffs, target, raw_id, protected_only=False):
    completed = []
    for bound in handoffs:
        if (bound.get("event") != "worker_bound" or bound.get("request_id") != raw_id
                or bound.get("direction") != "STORE" or bound.get("start") != 0
                or bound.get("end") != target["end"]
                or bound.get("prefix_sha256") != target["prefix_sha256"]
                or (protected_only and bound.get("source") != "protection")):
            continue
        receipts = [r for r in handoffs if r.get("event") == "scheduler_receipt"
                    and r.get("batch_id") == bound["batch_id"]
                    and r.get("wire_id") == bound["wire_id"] and r.get("failed") is False]
        transfers = successful_transfers(service, bound, "store")
        completed.extend(max(receipt["monotonic_ns"], transfer["monotonic_ns"])
                         for receipt in receipts for transfer in transfers)
    return min(completed, default=None)


def _generation_route(target, event, identity, generation, matches, operations, service):
    wire = generation["wire_id"]
    lookups = sorted((r for r in matches if r.get("wire_id") == wire
                      and r.get("request_id") == identity["raw_request_id"]
                      and r["monotonic_ns"] >= generation["monotonic_ns"]),
                     key=lambda r: r["monotonic_ns"])
    resolved = [r for r in lookups if r.get("external_tokens") is not None]
    # Earlier None observations are asynchronous polling, not a cache miss.
    lookup = resolved[0] if resolved else (lookups[0] if lookups else None)
    if lookup is None:
        return dict(wire_id=wire, route="lookup_unobserved", cache_match_ns=None)
    gpu, external = lookup["gpu_computed_tokens"], lookup.get("external_tokens")
    retrievals = [r for r in operations if r.get("wire_id") == wire
                  and r.get("request_id") == identity["raw_request_id"]
                  and r.get("direction") == "RETRIEVE"
                  and r["start"] + r["skip"] <= gpu < target["end"] <= r["end"]
                  and r["end"] <= len(event["prompt"])
                  and r["prefix_sha256"] == prefix_sha(event["prompt"][:r["end"]])]
    completed = [r for r in retrievals if successful_transfers(service, r, "retrieve")]
    if gpu >= target["end"]:
        route = "gpu_prefix_hit"
    elif completed:
        route = "lmcache_cpu_retrieve"
    elif retrievals:
        route = "retrieve_unconfirmed"
    elif external is None:
        route = "lookup_unresolved"
    elif gpu + external >= target["end"]:
        route = "external_hit_unconfirmed"
    else:
        route = "recompute_prefix"
    return dict(wire_id=wire, route=route, cache_match_ns=lookup["monotonic_ns"],
        lookup_observations=len(lookups), lookup_resolved=bool(resolved),
        lookup_state_changed=len({(r["gpu_computed_tokens"], r["external_tokens"]) for r in resolved}) > 1,
        gpu_computed_tokens=gpu, external_tokens=external,
        uncovered_prefix_tokens=None if external is None else max(0, target["end"] - gpu - external),
        matching_retrieve_ops=len(retrievals), completed_retrieve_ops=len(completed))


def analyze_rows(trace, requests, handoffs, service, label, targets=None):
    events = trace["events"]
    by_id = {event["event_id"]: event for event in events}
    results = requests["results"]
    result_by_id = {row["event_id"]: row for row in results}
    registered = {}
    for row in handoffs:
        if row.get("event") == "generation_registered" and row.get("role") == "SCHEDULER":
            registered.setdefault(row["request_id"], []).append(row)
    request_identity, failures = {}, []
    if len(results) != len(events) or set(result_by_id) != set(by_id):
        failures.append("Replay result IDs do not exactly cover trace events")
    for event_id in by_id:
        api_id = f"cachepilot-trace-{event_id:04d}"
        result = result_by_id.get(event_id)
        raw_id = f"cmpl-{api_id}-0"
        if (result is None or result.get("api_request_id") != api_id
                or result.get("response_id") != f"cmpl-{api_id}"
                or result.get("error") is not None):
            failures.append(f"API identity or completion mismatch for event {event_id}")
        generations = sorted(registered.get(raw_id, []), key=lambda r: r["monotonic_ns"])
        if not generations:
            failures.append(f"No scheduler generation for event {event_id} ({raw_id})")
        request_identity[event_id] = dict(api_request_id=api_id, raw_request_id=raw_id,
            response_id=None if result is None else result.get("response_id"),
            wire_ids=[r["wire_id"] for r in generations], generation_count=len(generations))

    protected = []
    for row in handoffs:
        if row.get("event") != "worker_bound" or row.get("role") != "WORKER" or row.get("source") != "protection":
            continue
        source_id = next((i for i, identity in request_identity.items()
                          if identity["raw_request_id"] == row.get("request_id")), None)
        if source_id is None:
            failures.append("Protected STORE raw request ID has no trace identity")
            continue
        tokens = list(by_id[source_id]["prompt"][:row["end"]])
        if len(tokens) != row["end"] or prefix_sha(tokens) != row["prefix_sha256"]:
            failures.append("Protected STORE token prefix mismatch")
        protected.append(dict(event_id=source_id, request_id=row["request_id"],
            wire_id=row["wire_id"], batch_id=row["batch_id"], prefix_sha256=row["prefix_sha256"],
            end=row["end"], token_prefix=tokens))

    # Never import another cell's wire IDs, batch IDs or timestamps.
    logical = {}
    for target in protected if targets is None else targets:
        key = (target["event_id"], target["end"], target["prefix_sha256"])
        tokens = list(by_id[target["event_id"]]["prompt"][:target["end"]])
        if tokens != target["token_prefix"] or prefix_sha(tokens) != target["prefix_sha256"]:
            failures.append("Cross-cell target differs from this trace")
        logical[key] = dict(event_id=key[0], end=key[1], prefix_sha256=key[2], token_prefix=tokens)
    matches = [r for r in handoffs if r.get("event") == "request_cache_match" and r.get("role") == "SCHEDULER"]
    operations = [r for r in handoffs if r.get("event") == "scheduler_metadata" and r.get("role") == "SCHEDULER"]
    routes = []
    for target in logical.values():
        source_id = target["event_id"]
        raw_id = request_identity[source_id]["raw_request_id"]
        protected_done = _store_completion(service, handoffs, target, raw_id, protected_only=True)
        store_done = _store_completion(service, handoffs, target, raw_id)
        for event in events:
            if (event["arrival_ms"] <= by_id[source_id]["arrival_ms"]
                    or len(event["prompt"]) < target["end"]
                    or event["prompt"][:target["end"]] != target["token_prefix"]):
                continue
            identity = request_identity[event["event_id"]]
            generations = [_generation_route(target, event, identity, generation, matches, operations, service)
                           for generation in registered.get(identity["raw_request_id"], [])]
            if not generations or any(g["route"] == "lookup_unobserved" for g in generations):
                failures.append(f"No cache-match observation for later event {event['event_id']}")
            first_ns = min((g["cache_match_ns"] for g in generations if g["cache_match_ns"] is not None), default=None)
            kinds = {g["route"] for g in generations}
            route = next(iter(kinds)) if len(kinds) == 1 else "mixed_generation_routes"
            routes.append(dict(source_event_id=source_id, event_id=event["event_id"],
                protected_end=target["end"], prefix_sha256=target["prefix_sha256"], route=route,
                generations=generations, store_completed_before_cache_match=(store_done is not None
                    and first_ns is not None and store_done < first_ns),
                protected_store_completed_ns=protected_done,
                protected_store_completed_before_cache_match=None if protected_done is None or first_ns is None else protected_done < first_ns))
    return dict(label=label, passed=not failures, failures=failures,
        trace_events=len(events), replay_results=len(results),
        mapped_requests=sum(bool(row["wire_ids"]) for row in request_identity.values()),
        cache_match_observations=len(matches), retrieve_operations=sum(
            row.get("direction") == "RETRIEVE" for row in operations),
        protected_store_batches=protected, logical_targets=list(logical.values()), routes=routes,
        route_counts={route: sum(row["route"] == route for row in routes)
                      for route in sorted({r["route"] for r in routes})},
        request_identity=request_identity,
        scope="Mechanism diagnosis only; no latency/performance conclusion")


def analyze_cell(directory, label, targets=None):
    directory = Path(directory)
    return analyze_rows(json.loads((directory / "trace.json").read_text(encoding="utf-8")),
        json.loads((directory / "requests.json").read_text(encoding="utf-8")),
        read_rows(directory / "handoffs", "*.jsonl"),
        read_rows(directory / "owned-events", "*.jsonl"), label, targets)


def reuse_gaps(trace, handoffs, service):
    """Find first resolved cache-match gaps against earlier prompts.

    This is offline opportunity analysis, never runtime input. A lookup can
    change while waiting and on preemption; neither gaps nor their sums prove
    executed recomputation. Successful STORE ranges do not prove residency.
    """
    events, gaps = trace["events"], []
    for event in events:
        earlier = [s for s in events if s["arrival_ms"] < event["arrival_ms"]]
        def shared(s):
            return next((i for i, (a, b) in enumerate(zip(s["prompt"], event["prompt"])) if a != b),
                        min(len(s["prompt"]), len(event["prompt"]))) // 256 * 256
        source = max(earlier, key=lambda s: (shared(s), s["arrival_ms"]), default=None)
        if source is None or shared(source) == 0:
            continue
        raw_id = f"cmpl-cachepilot-trace-{event['event_id']:04d}-0"
        lookups = sorted((r for r in handoffs if r.get("event") == "request_cache_match"
                          and r.get("role") == "SCHEDULER"
                          and r.get("request_id") == raw_id and r.get("external_tokens") is not None),
                         key=lambda r: r["monotonic_ns"])
        if not lookups:
            continue
        first, end = lookups[0], shared(source)
        gap = max(0, end - first["gpu_computed_tokens"] - first["external_tokens"])
        if gap == 0:
            continue
        stores = []
        for op in handoffs:
            if (op.get("event") != "worker_bound" or op.get("role") != "WORKER"
                    or op.get("direction") != "STORE"
                    or op.get("request_id") != f"cmpl-cachepilot-trace-{source['event_id']:04d}-0"
                    or op["end"] > len(source["prompt"])
                    or op["prefix_sha256"] != prefix_sha(source["prompt"][:op["end"]])):
                continue
            receipts = [r for r in handoffs if r.get("event") == "scheduler_receipt"
                        and r.get("role") == "SCHEDULER"
                        and r.get("batch_id") == op["batch_id"]
                        and r.get("wire_id") == op["wire_id"]
                        and r.get("request_id") == op["request_id"]
                        and r.get("direction") == "STORE" and r.get("failed") is False]
            completed = [max(r["monotonic_ns"], t["monotonic_ns"])
                         for r in receipts for t in successful_transfers(service, op, "store")]
            before = [n for n in completed if n < first["monotonic_ns"]]
            if before:
                stores.append(dict(start=op["start"], end=op["end"], completed_ns=min(before),
                                   batch_id=op["batch_id"]))
        variants = list(dict.fromkeys((r["wire_id"], r["gpu_computed_tokens"], r["external_tokens"])
                                     for r in lookups))
        gaps.append(dict(source_event_id=source["event_id"], event_id=event["event_id"],
            reusable_full_chunk_tokens=end, first_resolved_cache_match_ns=first["monotonic_ns"],
            first_gpu_tokens=first["gpu_computed_tokens"], first_external_tokens=first["external_tokens"],
            first_cache_match_uncovered_tokens=gap, source_successful_store_ranges_before_cache_match=stores,
            lookup_variants=[dict(wire_id=w, gpu_tokens=g, external_tokens=e) for w, g, e in variants],
            scope="First resolved cache-match opportunity only; not lookup issue time, executed work, residency or latency"))
    return gaps
