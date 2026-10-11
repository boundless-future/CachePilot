"""Audit physical-capacity fallback of an otherwise eligible STORE candidate."""
import argparse
import json
from pathlib import Path


def identity(row):
    return (row["wire_id"], row["prefix_sha256"], row["start"], row["end"])


def audit_rows(rows):
    rows = sorted(rows, key=lambda row: row["monotonic_ns"])
    fallbacks = [row for row in rows if row["event"] == "protection_fallback"
                 and row.get("reason") == "capacity"]
    assert fallbacks, "No physical-capacity fallback observed"
    for row in fallbacks:
        assert row["required_blocks"] in (16, 32)
        assert row["available_blocks"] >= row["required_blocks"]
        assert row["inflight_blocks"] == 0
        assert row["budget_blocks"] == 32
        assert row["free"] < row["reserve"] + row["required_blocks"]
        assert row["acquired_pins"] == 0
        assert row["start"] == 0 and row["end"] == row["required_blocks"] * 16
    saved = [row for row in fallbacks if any(
        later["event"] == "scheduler_built"
        and later.get("source") == "default_manager"
        and identity(later) == identity(row)
        and later["monotonic_ns"] > row["monotonic_ns"] for later in rows)]
    assert saved, "No capacity-declined candidate later saved by default manager"
    return dict(passed=True, fallback_observations=len(fallbacks),
                distinct_candidates=len({identity(row) for row in fallbacks}),
                default_saved_candidates=len({identity(row) for row in saved}),
                first_fallback_wire_id=fallbacks[0]["wire_id"],
                acquired_protection_pins=0)


def audit(directory):
    rows = [json.loads(line) for path in (directory / "events").glob("handoff-*.jsonl")
            for line in path.read_text().splitlines()]
    result = audit_rows(rows)
    driver = json.loads((directory / "capacity-driver.json").read_text())
    assert driver["fallback"]["wire_id"] == result["first_fallback_wire_id"]
    assert driver["pressure_requests"] == 2 and driver["stream_closed_after_fallback"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
