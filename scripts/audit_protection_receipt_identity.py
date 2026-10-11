"""Require rejected scheduler envelopes to conserve original pinned state."""
import argparse
import json
from pathlib import Path


def audit_rows(rows):
    target = [r for r in rows if r["event"] == "shared_target"]
    assert len(target) == 1
    target = target[0]
    bid, rid = target["batch_id"], target["request_id"]
    ids = set(map(str, target["block_ids"][0]))
    assert len(ids) == 32

    def one(event, case=None):
        found = [r for r in rows if r["event"] == event and r.get("batch_id") == bid
                 and (case is None or r.get("case") == case)]
        assert len(found) == 1, (event, case, len(found))
        return found[0]

    stale = one("receipt_identity_rejected", "synthetic_stale_batch")
    duplicate = one("receipt_identity_rejected", "duplicate_after_terminal")
    installed, ready, opened, delivered = (one(e) for e in
        ("shared_gate_installed", "shared_actual_ready", "shared_gate_opened", "shared_result_delivered"))
    worker, protection, scheduler = (one(e) for e in
        ("worker_receipt", "protection_receipt", "scheduler_receipt"))
    receipt_before, receipt_after = (one(e) for e in ("shared_receipt_before", "shared_receipt_after"))
    for row in (stale, duplicate):
        assert row["request_id"] == rid and row["before"] == row["after"]
        state = row["before"]
        assert set(map(str, state["refs"])) == ids
        assert set(map(str, state["hashes"])) == ids and all(state["hashes"].values())
    assert stale["injected_batch_id"] != bid and "stale" in stale["error"].lower()
    assert stale["before"]["ledger_batch_id"] == bid
    assert stale["before"]["protection_inflight"] and stale["before"]["manager_inflight"]
    assert stale["before"]["native_receipt_calls"] >= 0
    assert all(v == 1 for v in stale["before"]["refs"].values())
    assert duplicate["injected_batch_id"] == bid and "no original" in duplicate["error"].lower()
    assert duplicate["before"]["ledger_batch_id"] is None
    assert not duplicate["before"]["protection_inflight"] and not duplicate["before"]["manager_inflight"]
    assert duplicate["before"]["native_receipt_calls"] >= 1
    assert all(v == 0 for v in duplicate["before"]["refs"].values())
    assert all(v == 1 for v in receipt_before["refs"].values())
    assert all(v == 0 for v in receipt_after["refs"].values())
    assert delivered["actual_result"] is True and protection["released_pins"] == len(ids)
    assert not any(r["failed"] for r in (worker, protection, scheduler))
    ordered = (target, stale, installed, ready, opened, delivered, worker, receipt_before,
        protection, scheduler, receipt_after, duplicate)
    assert all(a["monotonic_ns"] < b["monotonic_ns"] for a, b in zip(ordered, ordered[1:]))
    return dict(passed=True, original_batch_id=bid, protected_blocks=len(ids),
        rejected_cases=[stale["case"], duplicate["case"]],
        native_manager_unchanged_on_reject=True, original_receipt_once=True,
        actual_request_id_reuse=False)


def audit(directory):
    rows = [json.loads(line) for p in (directory / "events").glob("handoff-*.jsonl")
            for line in p.read_text().splitlines()]
    result = audit_rows(rows)
    try:
        from scripts.audit_bounded_kv import audit as audit_sources
    except ModuleNotFoundError:
        from audit_bounded_kv import audit as audit_sources
    sources = [r for r in audit_sources(directory)["strict_sources"]
               if r["batch_id"] == result["original_batch_id"]]
    assert {(r["start"], r["end"]) for r in sources} == {(0, 256), (256, 512)}
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
