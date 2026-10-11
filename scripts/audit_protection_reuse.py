"""Require actual raw ID reuse, orphan isolation and rejected old envelopes."""
import argparse
import json
from pathlib import Path


def audit_rows(rows, driver, old_failed):
    def one(event, **fields):
        found = [r for r in rows if r["event"] == event
                 and all(r.get(k) == v for k, v in fields.items())]
        assert len(found) == 1, (event, fields, len(found))
        return found[0]

    old = one("reuse_target")
    bid, rid, wire = old["batch_id"], old["request_id"], old["wire_id"]
    assert old["source"] == "protection" and old["end"] == 512
    assert driver["actual_request_id_reuse"] and driver["vllm_request_id_randomization_disabled"]
    assert "cmpl-" + driver["payload"]["request_id"] + "-0" == rid
    assert driver["response"]["id"] + "-0" == rid
    arrived = one("reuse_new_generation", request_id=rid)
    assert arrived["old_wire_id"] == wire and arrived["new_wire_id"] != wire
    assert arrived["new_wire_id"].split(":", 4)[4] == rid
    assert arrived["new_wire_id"].split(":")[1] == wire.split(":")[1]
    assert int(arrived["new_wire_id"].split(":")[2]) > int(wire.split(":")[2])
    assert arrived["new_wire_id"].split(":")[3] != wire.split(":")[3]
    finished = one("reuse_new_finished", request_id=rid)
    assert finished["status"] == "FINISHED_LENGTH_CAPPED"
    before, after = (one(e, batch_id=bid) for e in ("reuse_receipt_before", "reuse_receipt_after"))
    for row in (arrived, finished, before):
        state = row["state"]
        assert state["orphaned"] and state["manager_inflight"] and state["protection_inflight"]
        assert state["ledger"]["batch_id"] == bid and not state["broken"]
        assert all(state["refs"][str(i)] == 1 for i in old["block_ids"][0])
    assert before["state"]["pending"]
    assert all(p["wire_id"] == arrived["new_wire_id"] for p in before["state"]["pending"])
    assert after["state"]["pending"] == before["state"]["pending"]
    assert not after["state"]["broken"] and not after["state"]["manager_inflight"]
    assert not after["state"]["protection_inflight"] and after["state"]["ledger"] is None
    assert after["state"]["native_receipt_calls"] == before["state"]["native_receipt_calls"] + 1
    assert all(after["state"]["refs"][str(i)] == 0 for i in old["block_ids"][0])
    new = one("reuse_new_batch", request_id=rid)
    assert new["wire_id"] == arrived["new_wire_id"] and new["batch_id"] != bid
    assert new["prefix_sha256"] != old["prefix_sha256"]
    old_receipt = one("scheduler_receipt", batch_id=bid)
    new_receipt = one("scheduler_receipt", batch_id=new["batch_id"])
    assert old_receipt["failed"] == old_failed and not new_receipt["failed"]
    delivered = one("reuse_result", batch_id=bid)
    assert delivered["actual_result"] is True and delivered["delivered_result"] == (not old_failed)
    rejects = [r for r in rows if r["event"] == "reuse_old_rejected"]
    assert len(rejects) == 4
    assert {r["case"] for r in rejects} == {"old_success", "old_failure",
        "duplicate_old_success", "duplicate_after_old_terminal"}
    for row in rejects:
        assert row["old_batch_id"] == bid and row["request_id"] == rid
        assert row["before"] == row["after"]
        assert row["monotonic_ns"] > after["monotonic_ns"]
        if row["new_batch_id"] is not None:
            assert row["new_batch_id"] == new["batch_id"]
            assert row["before"]["ledger"]["batch_id"] == new["batch_id"]
            assert row["before"]["manager_inflight"] and not row["before"]["broken"]
            assert "stale" in row["error"].lower()
            assert all(row["before"]["refs"][str(i)] >= 1 for i in new["block_ids"][0])
        else:
            assert row["before"]["ledger"] is None and "no original" in row["error"].lower()
    ordered = (old, arrived, finished, before, old_receipt, after, new, new_receipt)
    assert all(a["monotonic_ns"] < b["monotonic_ns"] for a, b in zip(ordered, ordered[1:]))
    return dict(passed=True, actual_request_id_reuse=True, old_batch_id=bid,
        new_batch_id=new["batch_id"], new_source=new["source"], old_receipt_reported_failed=old_failed,
        actual_store_success=True, new_pending_preserved=True, rejected_old_envelopes=len(rejects),
        vllm_request_id_randomization_disabled=True)


def audit(directory):
    rows = [json.loads(line) for p in (directory / "events").glob("handoff-*.jsonl")
            for line in p.read_text().splitlines()]
    driver = json.loads((directory / "reuse-driver.json").read_text())
    old_failed = next(r for r in rows if r["event"] == "reuse_result")["delivered_result"] is False
    result = audit_rows(rows, driver, old_failed)
    service = [json.loads(line) for p in (directory / "events").glob("owned-service-*.jsonl")
               for line in p.read_text().splitlines()]
    probes = [json.loads(line) for p in (directory / "probe").glob("kv-*.jsonl")
              for line in p.read_text().splitlines()]
    new = next(r for r in rows if r["event"] == "reuse_new_batch")
    bound = next(r for r in rows if r["event"] == "worker_bound" and r["batch_id"] == new["batch_id"])
    terminal = [r for r in service if r["event"] == "terminal_seen"
                and r["kind"] == "store" and r["request_id"] == new["wire_id"]]
    assert len(terminal) == 1
    stores = [r for r in probes if r["phase"] == "before_store"
              and r["request_id"] == new["request_id"]
              and bound["monotonic_ns"] < r["monotonic_ns"] < terminal[0]["monotonic_ns"]]
    assert {(r["start"], r["end"]) for r in stores} == {(0, 256), (256, 512)}
    restored = []
    for original in stores:
        loads = [r for r in probes if r["phase"] == "after_retrieve"
                 and r["token_prefix_sha256"] == original["token_prefix_sha256"]
                 and r["monotonic_ns"] > terminal[0]["monotonic_ns"]]
        assert len(loads) == 1, "Successor STORE was not actually restored"
        load = loads[0]
        assert {r["layer"]: r["sha256"] for r in original["layers"]} == {
            r["layer"]: r["sha256"] for r in load["layers"]}
        assert all(r.get("bitwise_equal") and r.get("reference_present") for r in load["layers"])
        assert not any(r["phase"] == "before_store"
            and r["token_prefix_sha256"] == original["token_prefix_sha256"]
            and original["monotonic_ns"] < r["monotonic_ns"] < load["monotonic_ns"] for r in probes)
        restored.append(dict(start=load["start"], end=load["end"], equal_layers=len(load["layers"])))
    result["successor_restored_chunks"] = restored
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
