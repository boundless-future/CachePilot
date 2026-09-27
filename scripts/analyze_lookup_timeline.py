"""Summarize the request-level ordering in a lookup-cancellation probe."""

import argparse
import json
from pathlib import Path


def summarize(events, result):
    times = result["timestamps_unix"]
    paused = times["server_paused"]
    resumed = times["server_resumed"]
    candidates = {
        row["request_id"] for row in events
        if row["event"] == "rpc_submit" and row.get("method") == "lookup"
        and paused <= row["unix_time"] < resumed
    }
    if len(candidates) != 1:
        raise ValueError(f"Expected one lookup while paused, found {sorted(candidates)}")
    request_id = candidates.pop()
    rows = sorted((row for row in events if row["request_id"] == request_id),
                  key=lambda row: row["monotonic_ns"])

    def first(event, method, after=0):
        return next((row for row in rows if row["monotonic_ns"] > after
                     and row["event"] == event and row.get("method") == method), None)

    lookup = first("rpc_submit", "lookup")
    cleanup_before = first("adapter_before", "cleanup_lookup_result",
                           lookup["monotonic_ns"])
    cleanup_after = first("adapter_after", "cleanup_lookup_result",
                          cleanup_before["monotonic_ns"]) if cleanup_before else None
    end_session = first("rpc_submit", "end_session",
                        cleanup_after["monotonic_ns"]) if cleanup_after else None
    status_queries = [row for row in rows if row["event"] == "rpc_submit"
                      and row.get("method") == "query_prefetch_status"]
    before_state = cleanup_before["state"] if cleanup_before else {}
    after_state = cleanup_after["state"] if cleanup_after else {}
    ordered = bool(cleanup_before and cleanup_after and end_session)
    return dict(
        request_id=request_id,
        connector=result["connector"],
        lookup_submitted_unix=lookup["unix_time"],
        client_disconnected_unix=times["client_disconnected"],
        cleanup_before_unix=cleanup_before["unix_time"] if cleanup_before else None,
        cleanup_after_unix=cleanup_after["unix_time"] if cleanup_after else None,
        end_session_submitted_unix=end_session["unix_time"] if end_session else None,
        server_resumed_unix=resumed,
        unacked_before_cleanup=before_state.get("unacked_urls", []),
        unacked_after_cleanup=after_state.get("unacked_urls", []),
        status_query_count=len(status_queries),
        cleanup_dropped_unacked_before_end=bool(
            ordered and before_state.get("unacked_urls")
            and not after_state.get("unacked_urls")
            and cleanup_before["unix_time"] < end_session["unix_time"] < resumed
            and not status_queries),
        after_cancel=dict(
            l1_read_locked=result["cache_status_after_cancel"]["l1_read_locked"],
            active_prefetch_jobs=result["cache_status_after_cancel"]["active_prefetch_jobs"],
        ),
        after_engine_stop=dict(
            l1_read_locked=result["cache_status_after_engine_stop"]["l1_read_locked"],
            active_prefetch_jobs=result["cache_status_after_engine_stop"]["active_prefetch_jobs"],
        ),
        experiment_passed=result["passed"],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.loads((args.directory / "result.json").read_text(encoding="utf-8"))
    events = [json.loads(line)
              for path in (args.directory / "lookup-timeline").glob("lookup-*.jsonl")
              for line in path.read_text(encoding="utf-8").splitlines()]
    summary = summarize(events, result)
    output = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
