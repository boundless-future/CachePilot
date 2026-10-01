"""Audit closed real-service evidence, including exact native token disposition."""
import argparse
from collections import Counter
import json
from pathlib import Path


def audit(directory):
    rows = []
    acquired, released = {}, {}
    service_files = sorted((directory / "events").glob("owned-service-*.jsonl"))
    assert service_files, "No real ownership service evidence"
    for path in service_files:
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert not any(r["event"] == "ownership_unresolved" for r in records)
        assert sum(r["event"] == "owned_shutdown_drained" for r in records) == 1
        submitted, terminal, retired = {}, {}, {}
        jobs, reclaimed = Counter(), Counter()
        for r in records:
            event = r["event"]
            if event in ("reservation_acquired", "reservation_released"):
                identity = (path.name, r["lock_id"], r["epoch"], r["serial"])
                target = acquired if event == "reservation_acquired" else released
                assert identity not in target, ("Duplicate native reservation event", identity)
                target[identity] = r
                if event == "reservation_released":
                    assert identity in acquired and r["monotonic_ns"] > acquired[identity]["monotonic_ns"]
                    assert r["disposition"] in ("RELEASED", "STALE_EPOCH")
            elif event in ("transfer_submitted", "terminal_seen", "transfer_retired"):
                target = {"transfer_submitted": submitted, "terminal_seen": terminal,
                          "transfer_retired": retired}[event]
                identity = (r["request_id"], r["sequence"], r["kind"])
                assert identity not in target
                target[identity] = r
            elif event == "lookup_registered":
                jobs[r["request_id"]] += 1
            elif event == "lookup_reclaimed":
                reclaimed[r["request_id"]] += 1
        assert jobs == reclaimed and all(n == 1 for n in jobs.values())
        assert submitted.keys() == terminal.keys() == retired.keys(), "Missing actual CUDA terminal"
        for identity, start in submitted.items():
            end, done = terminal[identity], retired[identity]
            assert start["monotonic_ns"] < end["monotonic_ns"] < done["monotonic_ns"]
            if start["kind"] == "retrieve":
                original = [r for token, r in acquired.items()
                            if token[0] == path.name and r["request_id"] == start["request_id"]
                            and r["monotonic_ns"] < start["monotonic_ns"]
                            and released[token]["monotonic_ns"] > start["monotonic_ns"]]
                assert len(original) == start["read_pins"]
                for r in original:
                    token = (path.name, r["lock_id"], r["epoch"], r["serial"])
                    assert released[token]["monotonic_ns"] > end["monotonic_ns"]
        rows.extend(records)
    assert acquired and acquired.keys() == released.keys(), "Unreleased original reservation"
    snapshots = {}
    for path in sorted((directory / "events").glob("owned-connector-*.jsonl")):
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records = [r for r in records if r["event"] == "scheduler_snapshot"]
        if records:
            final = records[-1]
            assert final["free_blocks"] == records[0]["free_blocks"]
            assert not final["registered_ids"] and not final["tracked_refs"] and not final["deferred_frees"]
            snapshots[path.name] = final["free_blocks"]
    assert snapshots, "No actual BlockPool evidence"
    return dict(passed=True, service_processes=len(service_files), original_tokens=len(acquired),
        dispositions=dict(Counter(r["disposition"] for r in released.values())),
        transfer_terminals=sum(r["event"] == "terminal_seen" for r in rows),
        final_free_blocks=snapshots, exactly_once=True, native_terminal_before_release=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
