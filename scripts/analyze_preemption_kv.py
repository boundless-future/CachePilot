"""Require whole retrieved chunks sourced from an orphaned STORE batch."""
from analyze_kv_probe import summarize_probe


def audit_probe_sources(probe_rows, lifecycle_rows):
    # Layout records share the KV JSONL but contain no chunk or byte comparison.
    phases = {"worker_layout", "before_store", "after_retrieve"}
    assert all(r["phase"] in phases for r in probe_rows), "Unexpected KV probe phase"
    probe_rows = [r for r in probe_rows if r["phase"] != "worker_layout"]
    summary = summarize_probe(probe_rows)
    receipts = sorted((r for r in lifecycle_rows if r["event"] == "scheduler_receipt_before"),
                      key=lambda r: r["monotonic_ns"])
    sources, covered, store_counts = {}, [], {}
    for row in sorted(probe_rows, key=lambda r: r["monotonic_ns"]):
        key = row["token_prefix_sha256"]
        if row["phase"] == "before_store":
            # DiagnosticConnector keeps the first source for each token key.
            sources.setdefault(key, row)
            store_counts[key] = store_counts.get(key, 0) + 1
        elif row["phase"] == "after_retrieve" and key in sources:
            source = sources[key]
            receipt = next((r for r in receipts if r["request_id"] == source["request_id"]
                           and r["monotonic_ns"] > source["monotonic_ns"]), None)
            if (receipt and receipt["orphaned"] and not receipt["failed"]
                    and receipt["monotonic_ns"] < row["monotonic_ns"]
                    and set(map(str, source["block_ids"])) <= set(receipt["pinned_refs"])):
                covered.append(dict(source_request_id=source["request_id"],
                    restored_request_id=row["request_id"], start=row["start"], end=row["end"],
                    token_prefix_sha256=key, compared_layers=len(row["layers"]),
                    intervening_stores=store_counts[key] - 1))
    unique = [r for r in covered if r["intervening_stores"] == 0]
    return dict(kv=summary, orphan_source_chunks=covered,
                unambiguous_orphan_source_chunks=unique,
                passed=summary["covered_and_equal"] and bool(unique))
