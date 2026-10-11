"""Audit real natural preemption coexisting with a pinned original STORE."""
import argparse
import json
from pathlib import Path


def audit_rows(rows, pressure_ids):
    targets = [r for r in rows if r["event"] == "shared_target"]
    assert len(targets) == 1
    target = targets[0]
    bid, rid = target["batch_id"], target["request_id"]
    relevant = sorted((r for r in rows if r.get("batch_id") == bid), key=lambda r: r["monotonic_ns"])

    def named(event):
        return [r for r in relevant if r["event"] == event]

    def one(event):
        found = named(event)
        assert len(found) == 1, (event, len(found))
        return found[0]

    installed, ready = one("shared_gate_installed"), one("shared_actual_ready")
    opened, delivered = one("shared_gate_opened"), one("shared_result_delivered")
    worker, protection, scheduler = one("worker_receipt"), one("protection_receipt"), one("scheduler_receipt")
    before_receipt, after_receipt = one("shared_receipt_before"), one("shared_receipt_after")
    ids = set(map(str, target["block_ids"][0]))
    assert len(ids) == target["end"] // 16 == 32
    assert len(pressure_ids) == 4 and rid not in pressure_ids
    before, after = named("protected_preempt_before"), named("protected_preempt_after")
    assert before and len(before) == len(after), "No complete natural preemption under protection"
    states = named("protected_pressure_state")
    assert states
    expected_hashes = {str(k): v for k, v in states[0]["hashes"].items()}
    assert set(expected_hashes) == ids and all(expected_hashes.values())

    def held(row):
        assert row["batch_id"] == bid and row["original_request_id"] == rid
        assert set(map(str, row["refs"])) == ids and all(v == 1 for v in row["refs"].values())
        assert {str(k): v for k, v in row["hashes"].items()} == expected_hashes
        assert row["manager_inflight"]

    for b, a in zip(before, after):
        assert b["request_id"] == a["request_id"] and b["request_id"] in pressure_ids
        assert b["status"] == "RUNNING" and a["status"] == "PREEMPTED"
        assert a["num_preemptions"] == b["num_preemptions"] + 1 and a["computed_tokens"] == 0
        assert ids.isdisjoint(map(str, b["victim_blocks"])), "This experiment expects unrelated victims"
        assert b["victim_blocks"] and b["computed_tokens"] > 0
        assert installed["monotonic_ns"] < b["monotonic_ns"] < a["monotonic_ns"] < opened["monotonic_ns"]
        held(b)
        held(a)
    allocations = named("protected_pressure_allocation")
    assert allocations and any(r["monotonic_ns"] > before[0]["monotonic_ns"] for r in allocations)
    for row in allocations:
        held(row)
        held(row["after"])
        assert ids.isdisjoint(map(str, row["allocated_ids"])), "Allocator reused a pinned block"
    for row in states:
        held(row)
    finished = [r for r in named("protected_pressure_finished") if r["request_id"] in pressure_ids]
    assert len(finished) == 4 and {r["request_id"] for r in finished} == set(pressure_ids)
    assert sum(r["num_preemptions"] for r in finished) == len(before)
    for row in finished:
        assert row["status"] == "FINISHED_ABORTED" and row["monotonic_ns"] < opened["monotonic_ns"]
        held(row)
    for row, expected in ((before_receipt, 1), (after_receipt, 0)):
        assert set(map(str, row["refs"])) == ids and all(v == expected for v in row["refs"].values())
    assert not after_receipt["protection_inflight"] and not after_receipt["manager_inflight"]
    assert delivered["actual_result"] is True and protection["released_pins"] == len(ids)
    for row in (installed, ready, delivered, worker, protection, scheduler, before_receipt, after_receipt):
        assert row["request_id"] == rid
    assert not any(r["failed"] for r in (worker, protection, scheduler))
    ordered = (target, installed, opened, delivered, worker, before_receipt, protection, scheduler, after_receipt)
    assert all(a["monotonic_ns"] < b["monotonic_ns"] for a, b in zip(ordered, ordered[1:]))
    assert installed["monotonic_ns"] < ready["monotonic_ns"] < opened["monotonic_ns"]
    return dict(passed=True, batch_id=bid, protected_blocks=len(ids), preemptions=len(before),
        preempted_requests=len({r["request_id"] for r in before}),
        observed_allocations=len(allocations), shared_prefix_victim=False,
        protected_hashes_unchanged=True, original_receipt_releases_once=True)


def audit(directory):
    rows = [json.loads(line) for p in (directory / "events").glob("handoff-*.jsonl")
            for line in p.read_text().splitlines()]
    driver = json.loads((directory / "protected-preemption-driver.json").read_text())
    result = audit_rows(rows, driver["pressure_request_ids"])
    try:
        from scripts.audit_bounded_kv import audit as audit_sources
    except ModuleNotFoundError:
        from audit_bounded_kv import audit as audit_sources
    sources = [r for r in audit_sources(directory)["strict_sources"] if r["batch_id"] == result["batch_id"]]
    assert {(r["start"], r["end"]) for r in sources} == {(0, 256), (256, 512)}, "Missing original protected STORE-to-H2D provenance"
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
