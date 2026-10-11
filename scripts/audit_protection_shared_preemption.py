"""Require actual shared-prefix victim preemption under original STORE pin."""
import argparse
import json
from pathlib import Path


def audit_rows(rows, pressure_ids):
    targets = [r for r in rows if r["event"] == "shared_target"]
    assert len(targets) == 1
    target = targets[0]
    bid, rid = target["batch_id"], target["request_id"]
    relevant = [r for r in rows if r.get("batch_id") == bid]
    ids = set(map(str, target["block_ids"][0]))
    assert len(ids) == 32 and len(pressure_ids) == 4

    def named(event):
        return [r for r in relevant if r["event"] == event]

    def one(event):
        found = named(event)
        assert len(found) == 1, (event, len(found))
        return found[0]

    installed, ready, opened, delivered = (one(e) for e in
        ("shared_gate_installed", "shared_actual_ready", "shared_gate_opened", "shared_result_delivered"))
    worker, protection, scheduler = (one(e) for e in
        ("worker_receipt", "protection_receipt", "scheduler_receipt"))
    receipt_before, receipt_after = (one(e) for e in
        ("shared_receipt_before", "shared_receipt_after"))
    arrivals = [r for r in named("protected_pressure_arrived") if r["request_id"] in pressure_ids]
    assert len(arrivals) == 4
    shared = [r for r in arrivals if r["first_token"] == target["token_ids"][0]]
    assert len(shared) == 1
    shared_id = shared[0]["request_id"]
    assert shared_id != rid
    pre_before = [r for r in named("protected_preempt_before") if r["request_id"] == shared_id]
    pre_after = [r for r in named("protected_preempt_after") if r["request_id"] == shared_id]
    assert len(pre_before) == len(pre_after) == 1
    before, after = pre_before[0], pre_after[0]
    assert before["status"] == "RUNNING" and after["status"] == "PREEMPTED"
    assert after["num_preemptions"] == before["num_preemptions"] + 1
    assert before["computed_tokens"] >= 512 and after["computed_tokens"] == 0
    assert ids <= set(map(str, before["victim_blocks"]))
    assert set(map(str, before["refs"])) == set(map(str, after["refs"])) == ids
    assert all(v == 2 for v in before["refs"].values())
    assert all(v == 1 for v in after["refs"].values())
    assert before["hashes"] == after["hashes"] and all(before["hashes"].values())
    assert before["manager_inflight"] and after["manager_inflight"]
    assert installed["monotonic_ns"] < before["monotonic_ns"] < after["monotonic_ns"] < opened["monotonic_ns"]
    allocations = named("protected_pressure_allocation")
    assert allocations and any(r["monotonic_ns"] > after["monotonic_ns"] for r in allocations)
    for row in allocations:
        assert ids.isdisjoint(map(str, row["allocated_ids"]))
        assert row["hashes"] == before["hashes"] == row["after"]["hashes"]
        assert row["manager_inflight"] and row["after"]["manager_inflight"]
        assert all(v >= 1 for v in row["refs"].values())
        assert all(v >= 1 for v in row["after"]["refs"].values())
    finished = [r for r in named("protected_pressure_finished") if r["request_id"] in pressure_ids]
    assert len(finished) == 4 and {r["request_id"] for r in finished} == set(pressure_ids)
    assert all(r["status"] == "FINISHED_ABORTED" and
        r["monotonic_ns"] < opened["monotonic_ns"] for r in finished)
    assert all(v == 1 for v in receipt_before["refs"].values())
    assert all(v == 0 for v in receipt_after["refs"].values())
    assert not receipt_after["manager_inflight"] and not receipt_after["protection_inflight"]
    assert delivered["actual_result"] is True and protection["released_pins"] == 32
    assert not any(r["failed"] for r in (worker, protection, scheduler))
    ordered = (target, installed, before, after, opened, delivered, worker,
        receipt_before, protection, scheduler, receipt_after)
    assert all(a["monotonic_ns"] < b["monotonic_ns"] for a, b in zip(ordered, ordered[1:]))
    assert installed["monotonic_ns"] < ready["monotonic_ns"] < opened["monotonic_ns"]
    return dict(passed=True, batch_id=bid, shared_request_id=shared_id,
        protected_blocks=32, shared_refs_sequence=[2, 1, 0],
        allocations=len(allocations), shared_prefix_victim=True)


def audit(directory):
    rows = [json.loads(line) for p in (directory / "events").glob("handoff-*.jsonl")
            for line in p.read_text().splitlines()]
    driver = json.loads((directory / "protected-preemption-driver.json").read_text())
    assert driver["shared_prefix_victim_required"]
    result = audit_rows(rows, set(driver["pressure_request_ids"]))
    try:
        from scripts.audit_bounded_kv import audit as audit_sources
    except ModuleNotFoundError:
        from audit_bounded_kv import audit as audit_sources
    sources = [r for r in audit_sources(directory)["strict_sources"]
               if r["batch_id"] == result["batch_id"]]
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
