"""Pin/metadata/receipt conservation alongside the independent service audit."""
import json
from pathlib import Path


def audit_rows(rows, budget=32):
    prepared, committed = {}, {}
    pins = peak = stores = allocations = rollbacks = 0
    for row in sorted(rows, key=lambda r: r["monotonic_ns"]):
        event = row["event"]
        rid = row.get("wire_id")
        if event == "protection_prepared":
            assert rid not in prepared and rid not in committed
            assert row["start"] == 0 and row["end"] in (256, 512)
            assert row["acquired_pins"] == row["end"] // 16
            assert len(row["chunk_hashes"]) == row["end"] // 256
            assert row["free_after"] >= row["reserve"]
            prepared[rid] = row
            pins += row["acquired_pins"]
            peak = max(peak, pins)
            assert pins <= budget
        elif event == "protection_metadata_committed":
            old = prepared.pop(rid)
            assert row["scheduled_tokens"] > 0
            assert row["owned_pins"] == old["acquired_pins"]
            for key in ("block_ids", "prefix_sha256", "start", "end"):
                assert row[key] == old[key]
            committed[rid] = dict(row)
            stores += 1
        elif event == "protection_rollback":
            old = prepared.pop(rid)
            assert row["released_pins"] == old["acquired_pins"]
            pins -= row["released_pins"]
            rollbacks += 1
        elif event == "protection_allocation_boundary":
            expected = {b for p in prepared.values() for g in p["block_ids"] for b in g}
            assert expected == set(row["protected_ids"])
            assert expected.isdisjoint(row["allocated_ids"])
            allocations += 1
        elif event == "scheduler_built" and row["source"] == "protection":
            assert rid in committed
            assert "batch_id" not in committed[rid]
            committed[rid]["batch_id"] = row["batch_id"]
        elif event == "protection_receipt":
            old = committed.pop(rid)
            assert row["batch_id"] == old["batch_id"]
            assert row["released_pins"] == old["owned_pins"]
            pins -= row["released_pins"]
    assert not prepared and not committed and pins == 0
    assert stores > 0 and allocations > 0, "No actual preallocation protection exercised"
    return dict(passed=True, protection_stores=stores, peak_protection_blocks=peak,
                observed_allocations=allocations, rollbacks=rollbacks, final_owned_pins=pins)


def audit(directory):
    return audit_rows([json.loads(line) for p in Path(directory).glob("handoff-*.jsonl")
                      for line in p.read_text().splitlines()])
