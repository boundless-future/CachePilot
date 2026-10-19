"""Cross-check source 1 STORE ranges against event 9's confirmed CPU retrieve."""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(run, audit_path):
    audit = read(audit_path)
    reuse = audit["reuse"]
    handoff_paths = sorted((run / "handoffs").glob("*.jsonl"))
    rows = [json.loads(line) for path in handoff_paths
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    route = reuse["generations"][0]
    retrieve = next(row for row in rows
        if row.get("event") == "scheduler_metadata"
        and row.get("role") == "SCHEDULER"
        and row.get("direction") == "RETRIEVE"
        and row.get("wire_id") == route["wire_id"])
    retrieve_start = retrieve["start"] + retrieve["skip"]
    retrieve_end = retrieve["end"]
    stores = []
    for store in reuse["successful_source_store_ranges"]:
        intersections = []
        start, end = max(store["start"], retrieve_start), min(store["end"], retrieve_end)
        if start < end:
            intersections.append(dict(start=start, end=end, tokens=end - start))
        offered = next(row for row in rows
            if row.get("event") == "source1_candidate_offered"
            and row.get("start") == store["start"] and row.get("end") == store["end"])
        snapshot = min((row for row in rows
            if row.get("event") == "source1_window_snapshot"
            and row.get("stage") == "policy_add_after"
            and row.get("monotonic_ns", 0) >= offered["monotonic_ns"]
            and any(item["op"]["start"] == store["start"]
                    and item["op"]["end"] == store["end"] for item in row["pending"])),
            key=lambda row: row["monotonic_ns"])
        blocks = [block for item in snapshot["pending"]
                  if item["op"]["start"] == store["start"]
                  and item["op"]["end"] == store["end"]
                  for block in item["op"]["blocks"]]
        stores.append(dict(start=store["start"], end=store["end"],
            completed_ns=store["completed_ns"],
            retrieve_intersections=intersections,
            candidate_snapshot=dict(monotonic_ns=snapshot["monotonic_ns"],
                active=snapshot["active"], finished=snapshot["finished"],
                store_inflight=snapshot["store_inflight"],
                free_blocks=snapshot["free_blocks"], reserve_blocks=snapshot["reserve_blocks"],
                max_safe_protection_blocks=snapshot["max_safe_protection_blocks"],
                pending_block_count=len(blocks),
                ref_counts=sorted({block["ref_count"] for block in blocks}),
                free_ranks=sorted({block["free_rank"] for block in blocks},
                                  key=lambda rank: -1 if rank is None else rank))))
    intersection_tokens = sum(part["tokens"] for store in stores
                              for part in store["retrieve_intersections"])
    expected_tokens = route["external_tokens"]
    return dict(
        scope="Offline mechanism evidence from the source1-deadline diagnostic; not a performance attribution or policy input",
        source_event_id=reuse["source_event_id"], target_event_id=reuse["event_id"],
        target_prefix_tokens=reuse["target_tokens"],
        target_route=route["route"], target_gpu_tokens=route["gpu_computed_tokens"],
        target_external_tokens=expected_tokens,
        retrieve=dict(start=retrieve["start"], skip=retrieve["skip"],
                      effective_start=retrieve_start, end=retrieve_end,
                      completed=route["completed_retrieve_ops"] == 1),
        stores=stores,
        checks=dict(audit_complete=audit["complete"],
            single_confirmed_target_retrieve=(len(reuse["generations"]) == 1
                and route["route"] == "lmcache_cpu_retrieve"
                and route["matching_retrieve_ops"] == route["completed_retrieve_ops"] == 1
                and retrieve_start == route["gpu_computed_tokens"]
                and retrieve_end - retrieve_start == expected_tokens),
            full_source_prefix_stored=reuse["contiguous_saved_tokens"] == reuse["target_tokens"],
            store_completed_before_target_lookup=reuse["full_store_before_first_cache_match"],
            retrieve_intersections_equal_confirmed_external_tokens=intersection_tokens == expected_tokens,
            every_store_segment_intersects_retrieve=all(store["retrieve_intersections"] for store in stores),
            candidate_samples_show_active_request=all(store["candidate_snapshot"]["active"]
                                                       and not store["candidate_snapshot"]["finished"] for store in stores),
            blocks_not_free_ranked=all(all(rank is None for rank in store["candidate_snapshot"]["free_ranks"])
                                       for store in stores)),
        interpretation="The confirmed external interval [1664,4352) intersects all three source STORE ranges (384, 2048, and 256 tokens), together covering all 2688 external tokens. This sample provides no basis for dropping any one range. At the recorded candidate snapshots the source was active and its blocks had ref_count 1 with no free-queue rank; a completed-request/free-queue-only rule would not select them then. These snapshots do not prove that every possible online decision moment has the same state, nor establish DMA waste or causal latency.",
        inputs=dict(audit_sha256=digest(audit_path),
                    handoff_files={path.name: digest(path) for path in handoff_paths}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.run_directory, args.audit)
    if not all(result["checks"].values()):
        raise SystemExit("Source 1 retrieve/store cross-check failed: " +
                         json.dumps(result["checks"], sort_keys=True))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(result["checks"], sort_keys=True))


if __name__ == "__main__":
    main()
