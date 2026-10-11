"""Require a different eligible candidate to fall back while pins are in flight."""
import argparse
import json
from pathlib import Path


def audit_rows(rows):
    rows = sorted(rows, key=lambda r: r["monotonic_ns"])
    def one(event):
        found = [r for r in rows if r["event"] == event]
        assert len(found) == 1, (event, len(found))
        return found[0]

    target = one("shared_target")
    bid, rid = target["batch_id"], target["request_id"]
    installed = one("shared_gate_installed")
    opened = one("shared_gate_opened")
    delivered = one("shared_result_delivered")
    receipt = next(r for r in rows if r["event"] == "protection_receipt"
        and r.get("batch_id") == bid)
    fallbacks = [r for r in rows if r["event"] == "protection_fallback"
        and r.get("reason") == "inflight_budget"
        and rid in r["inflight_request_ids"]]
    assert fallbacks
    assert target["end"] == 512 and len(target["block_ids"][0]) == 32
    assert all(r["request_id"] != rid and r["inflight_blocks"] == 32
        and r["budget_blocks"] == 32 and r["available_blocks"] == 0
        and r["required_blocks"] in (16, 32) and r["acquired_pins"] == 0
        for r in fallbacks)
    assert installed["monotonic_ns"] < fallbacks[0]["monotonic_ns"] < opened["monotonic_ns"]
    assert opened["monotonic_ns"] < delivered["monotonic_ns"] < receipt["monotonic_ns"]
    assert delivered["actual_result"] is True and receipt["released_pins"] == 32
    identity = lambda r: (r["wire_id"], r["prefix_sha256"], r["start"], r["end"])
    first = fallbacks[0]
    default = [r for r in rows if r["event"] == "scheduler_built"
        and r["source"] == "default_manager" and identity(r) == identity(first)
        and r["monotonic_ns"] > first["monotonic_ns"]]
    assert len(default) == 1, "Declined candidate did not later traverse default manager"
    return dict(passed=True, target_batch_id=bid, distinct_fallbacks=len({r["wire_id"] for r in fallbacks}),
        first_fallback_wire_id=fallbacks[0]["wire_id"], in_flight_blocks=32,
        protected_pins_released=32, default_saved_fallback=True)


def audit(directory):
    rows = [json.loads(line) for p in (directory / "events").glob("handoff-*.jsonl")
        for line in p.read_text().splitlines()]
    result = audit_rows(rows)
    driver = json.loads((directory / "inflight-budget-driver.json").read_text())
    assert driver["target"]["batch_id"] == result["target_batch_id"]
    assert driver["fallback"]["wire_id"] == result["first_fallback_wire_id"]
    assert driver["gate_opened_after_budget_rejection"]
    try:
        from scripts.audit_bounded_kv import audit as audit_sources
    except ModuleNotFoundError:
        from audit_bounded_kv import audit as audit_sources
    sources = [r for r in audit_sources(directory)["strict_sources"]
        if r["batch_id"] == result["target_batch_id"]]
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
