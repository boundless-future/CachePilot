"""Require cancellation to release exactly the new request's shared references."""
import argparse
import json
from pathlib import Path


def audit_rows(rows):
    targets = [r for r in rows if r["event"] == "shared_target"]
    assert len(targets) == 1
    target = targets[0]
    bid, rid = target["batch_id"], target["request_id"]
    relevant = [r for r in rows if r.get("batch_id") == bid]

    def one(event):
        found = [r for r in relevant if r["event"] == event]
        assert len(found) == 1, (event, len(found))
        return found[0]

    installed, ready = one("shared_gate_installed"), one("shared_actual_ready")
    before, after = one("shared_free_before"), one("shared_free_after")
    opened, delivered = one("shared_gate_opened"), one("shared_result_delivered")
    receipt_before, receipt_after = one("shared_receipt_before"), one("shared_receipt_after")
    worker, protection, scheduler = one("worker_receipt"), one("protection_receipt"), one("scheduler_receipt")
    shared_rid = before["request_id"]
    assert shared_rid != rid and after["request_id"] == shared_rid
    assert before["original_request_id"] == after["original_request_id"] == rid
    assert before["status"] == after["status"] == "FINISHED_ABORTED"
    ids = set(map(str, target["block_ids"][0]))
    assert len(ids) == target["end"] // 16
    for row in (before, after):
        assert set(map(str, row["shared_ids"])) == set(map(str, row["protected_ids"])) == ids
    live = [r for r in relevant if r["event"] == "shared_live_refs" and r["request_id"] == shared_rid
            and installed["monotonic_ns"] < r["monotonic_ns"] < before["monotonic_ns"]]
    assert live and any(set(map(str, r["shared_ids"])) == ids
                        and set(map(str, r["refs"])) == ids
                        and all(v == 2 for v in r["refs"].values()) for r in live)
    for row, expected in ((before, 2), (after, 1), (receipt_before, 1), (receipt_after, 0)):
        assert set(map(str, row["refs"])) == ids
        assert all(value == expected for value in row["refs"].values())
    assert not after["allocation_present"] and after["protection_inflight"] and after["manager_inflight"]
    assert not receipt_after["protection_inflight"] and not receipt_after["manager_inflight"]
    assert delivered["actual_result"] is True
    assert not worker["failed"] and not protection["failed"] and not scheduler["failed"]
    assert protection["released_pins"] == len(ids)
    for row in (installed, ready, delivered, worker, protection, scheduler, receipt_before, receipt_after):
        assert row["request_id"] == rid
    ordered = (target, installed, before, after, opened, delivered, worker, receipt_before,
               protection, scheduler, receipt_after)
    assert all(a["monotonic_ns"] < b["monotonic_ns"] for a, b in zip(ordered, ordered[1:]))
    assert installed["monotonic_ns"] < ready["monotonic_ns"] < opened["monotonic_ns"]
    return dict(passed=True, batch_id=bid, original_request_id=rid,
        cancelled_request_id=shared_rid, shared_blocks=len(ids), refs_sequence=[2, 1, 0],
        real_store_success=True, original_receipt_releases_once=True)


def audit(directory):
    rows = [json.loads(line) for path in (directory / "events").glob("handoff-*.jsonl")
            for line in path.read_text().splitlines()]
    result = audit_rows(rows)
    from scripts.audit_bounded_kv import audit as audit_sources
    sources = audit_sources(directory)["strict_sources"]
    sources = [row for row in sources if row["batch_id"] == result["batch_id"]]
    assert {(row["start"], row["end"]) for row in sources} == {
        (start, start + 256) for start in range(0, result["shared_blocks"] * 16, 256)
    }, "Shared cancellation lacks complete strict protected STORE-to-H2D coverage"
    result["strict_sources"] = sources
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
