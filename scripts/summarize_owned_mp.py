"""Summarize real MP guard and vLLM BlockPool evidence from one smoke run."""

import argparse
import json
from pathlib import Path


def rows(directory, prefix):
    return [json.loads(line) for path in directory.glob(prefix + "-*.jsonl")
            for line in path.read_text().splitlines()]


def summarize(root, mode):
    events = root / "events"
    server = rows(events, "owned-mp")
    connector = rows(events, "owned-connector" if mode == "normal"
                     else "retrieve-failure")
    if mode == "normal":
        result = json.loads((root / "owned-mp" / "result.json").read_text())
        submitted = [row for row in server if row["event"] == "retrieve_submitted"]
        assert result["passed"] and result["retrieve_transfers"] > 0
        assert len(submitted) == 1
        request_id = submitted[0]["request_id"]
        related = [row for row in server if row["request_id"] == request_id]
        assert [row["event"] for row in related] == [
            "lookup_registered", "retrieve_submitted", "retrieve_rpc_result",
            "retrieve_native_completed", "session_ended"]
        assert related[0]["generation"] == related[1]["generation"] == related[3]["generation"]
        assert related[2]["succeeded"]
        status = json.loads((root / "owned-mp" /
                             "after-engine-stop-status.json").read_text())
        assert status["storage_manager"]["l1_manager"]["read_locked_count"] == 0
        assert status["active_prefetch_jobs"] == 0
    else:
        result = json.loads((root / "result.json").read_text())
        assert result["passed"] and result["outputs_equal"]
        request_id = result["lifecycle"]["request_id"]
        related = [row for row in server if row["request_id"] == request_id]
        assert [row["event"] for row in related] == [
            "lookup_registered", "retrieve_rejected_blocks", "session_ended"]
        assert result["lifecycle"]["baseline_free_blocks"] == result["lifecycle"]["final_free_blocks"]
        assert result["cache_status"]["l1_read_locked"] == 0
        assert result["cache_status_after_engine_stop"]["registered_gpu_ids"] == []

    snapshots = [row for row in connector if row["event"] == "scheduler_snapshot"]
    waiting = [row for row in snapshots if any(
        item["request_id"] == request_id and item["status"] == "WAITING_FOR_REMOTE_KVS"
        for item in row["requests"])]
    assert waiting
    settled = [row for row in snapshots if row["monotonic_ns"] > waiting[0]["monotonic_ns"]
               and not row["registered_ids"] and not row["tracked_refs"]
               and row["deferred_frees"] == 0]
    assert settled
    if mode == "underflow":
        assert settled[-1]["free_blocks"] == result["lifecycle"]["baseline_free_blocks"]
    return dict(mode=mode, passed=True, request_id=request_id,
                server_events=[row["event"] for row in related],
                generation=related[0]["generation"],
                waiting_snapshots=len(waiting),
                waiting_block_refs=max(len(row["tracked_refs"]) for row in waiting),
                final_free_blocks=settled[-1]["free_blocks"],
                final_tracked_refs=settled[-1]["tracked_refs"],
                final_deferred_frees=settled[-1]["deferred_frees"],
                external_hit_tokens=(result["external_hit_tokens"] if mode == "normal"
                                     else result["target_external_hit_delta"]),
                failed_blocks=(result["lifecycle"]["failed_blocks"]
                               if mode == "underflow" else 0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("normal", "underflow"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.root, args.mode)
    content = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(content)
    print(content, end="")


if __name__ == "__main__":
    main()
