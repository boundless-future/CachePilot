"""Offline audit: constructed metadata, worker submit and both receipt edges."""
from collections import Counter
import json
from pathlib import Path


STAGES = ("scheduler_built", "worker_bound", "worker_submit_returned",
          "worker_receipt", "scheduler_receipt")


def audit_rows(rows):
    stages = {stage: {} for stage in STAGES}
    for row in rows:
        stage = row["event"]
        if stage not in stages:
            continue
        identity = row["batch_id"]
        assert identity not in stages[stage], ("duplicate stage", stage, identity)
        stages[stage][identity] = row
    expected = stages[STAGES[0]]
    assert expected, "No real default STORE handoff"
    for stage in STAGES:
        assert stages[stage].keys() == expected.keys(), ("missing handoff edge", stage)
    semantic = Counter()
    for identity, original in expected.items():
        previous = -1
        for stage in STAGES:
            row = stages[stage][identity]
            assert row["monotonic_ns"] > previous
            previous = row["monotonic_ns"]
            for key in ("request_id", "wire_id", "direction", "start", "end", "skip",
                        "block_ids", "prefix_sha256", "source"):
                assert row[key] == original[key], ("changed handoff", stage, key)
        assert stages["worker_receipt"][identity]["failed"] == stages["scheduler_receipt"][identity]["failed"]
        assert original["source"] == "default_manager" and original["direction"] == "STORE"
        semantic[(original["prefix_sha256"], original["start"], original["end"])] += 1
    bypass = [r for r in rows if r["event"] == "protection_bypass"]
    assert all(r["added_stores"] == r["acquired_protection_pins"] == 0 for r in bypass)
    return dict(passed=True, batches=len(expected),
        failed_batches=sum(r["failed"] for r in stages["scheduler_receipt"].values()),
        bypass_decisions=len(bypass), added_protection_stores=0, acquired_protection_pins=0,
        store_signatures=[dict(prefix_sha256=k[0], start=k[1], end=k[2], count=n)
                          for k, n in sorted(semantic.items())])


def audit(directory):
    return audit_rows([json.loads(line) for p in sorted(Path(directory).glob("handoff-*.jsonl"))
                      for line in p.read_text().splitlines()])
