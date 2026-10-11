"""Validate the protected front half and native suffix of a split STORE."""


def audit_rows(rows):
    rows = sorted(rows, key=lambda row: row["monotonic_ns"])
    protected = [row for row in rows if row["event"] == "scheduler_built"
                 and row.get("source") == "protection"
                 and (row["start"], row["end"]) == (0, 512)]
    assert protected, "No protected [0, 512) STORE was emitted"
    receipts = {(row["wire_id"], row["batch_id"]): row for row in rows
                if row["event"] == "scheduler_receipt" and not row.get("failed")}
    native = [row for row in rows if row["event"] == "scheduler_built"
              and row.get("source") == "default_manager"
              and (row["start"], row["end"]) == (512, 1024)]
    matches = []
    for front in protected:
        receipt = receipts.get((front["wire_id"], front["batch_id"]))
        if receipt is None:
            continue
        suffixes = [row for row in native if row["wire_id"] == front["wire_id"]
                    ]
        if suffixes:
            assert len(suffixes) == 1, "Duplicate suffix STORE"
            suffix = suffixes[0]
            assert suffix["monotonic_ns"] > receipt["monotonic_ns"], "Suffix preceded front receipt"
            assert (suffix["wire_id"], suffix["batch_id"]) in receipts, "Suffix lacks success receipt"
            front_ids = {block_id for group in front["block_ids"] for block_id in group}
            suffix_ids = {block_id for group in suffix["block_ids"] for block_id in group}
            assert len(front_ids) == len(suffix_ids) == 32
            assert front_ids.isdisjoint(suffix_ids)
            matches.append(dict(wire_id=front["wire_id"], front_batch=front["batch_id"],
                                suffix_batch=suffix["batch_id"]))
    assert matches, "No native [512, 1024) suffix followed a protected front receipt"
    return dict(passed=True, split_chains=len(matches), chains=matches)


def audit(directory):
    if __package__:
        from .audit_bounded_kv import read_rows, audit_rows as audit_kv
    else:
        from audit_bounded_kv import read_rows, audit_rows as audit_kv
    rows = read_rows((directory / "events").glob("handoff-*.jsonl"))
    result = audit_rows(rows)
    splits = [row for row in rows if row["event"] == "protection_prefix_split"]
    assert splits, "Missing original operation/suffix accounting"
    for split in splits:
        assert split["original_end"] == 1024
        assert (split["suffix"]["start"], split["suffix"]["end"]) == (512, 1024)
        assert split["wire_id"] == split["suffix"]["wire_id"]
    failed = [row for row in rows if row["event"] == "protection_receipt" and row["failed"]]
    for receipt in failed:
        assert any(s["wire_id"] == receipt["wire_id"] for s in splits)
        assert receipt["pending_after"] == 0 and receipt["dropped_failed_store"] > 0
        assert not any(row["event"] == "scheduler_built" and row["wire_id"] == receipt["wire_id"]
                       and row["start"] >= 512 for row in rows)
    service = read_rows((directory / "events").glob("owned-service-*.jsonl"))
    probes = read_rows((directory / "probe").glob("kv-*.jsonl"))
    front_sources = audit_kv(rows, service, probes)["strict_sources"]
    suffix_sources = audit_kv(rows, service, probes, source="default_manager")["strict_sources"]
    covered = []
    for chain in result["chains"]:
        front = [r for r in front_sources if r["batch_id"] == chain["front_batch"]]
        suffix = [r for r in suffix_sources if r["batch_id"] == chain["suffix_batch"]]
        if {(r["start"], r["end"]) for r in front} == {(0, 256), (256, 512)} and {
                (r["start"], r["end"]) for r in suffix} == {(512, 768), (768, 1024)}:
            covered.append(dict(**chain, equal_layers=sum(r["equal_layers"] for r in front + suffix)))
    assert covered, "No full four-chunk split chain has strict STORE-to-H2D provenance"
    result.update(split_operations=len(splits), failed_suffixes_dropped=len(failed),
                  strict_complete_chains=covered)
    return result
