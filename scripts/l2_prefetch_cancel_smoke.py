"""Real FS L2 prefetch cancellation under controlled lookup/load timing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

import requests

from lifecycle_smoke import metric_summary, prompt
from preemption_smoke import completion
from remote_lookup_cancel_smoke import cache_state, deferred_requests, open_stream, wait_for
from validate_environment import ROOT, Service, get_text, write_json


def events(directory):
    rows = []
    for path in directory.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # A concurrently appended last line is read next poll.
    return rows


def drained(state):
    fields = ("l1_read_locked", "l1_write_locked", "active_prefetch_jobs",
              "abandoned_prefetch_jobs", "reclaim_failed_jobs", "reclaim_key_snapshots",
              "completed_results_count", "store_pending", "store_in_flight",
              "prefetch_in_flight", "prefetch_pending", "prefetch_lookup", "prefetch_load")
    return state["is_healthy"] and all(state[key] == 0 for key in fields)


def audit_events(rows, disconnected, reclaim):
    """Reject a run that missed either barrier or reclaimed the wrong objects."""
    if any(row["event"] == "gate_timeout" for row in rows):
        raise AssertionError("Diagnostic barrier timed out")
    rows = sorted(rows, key=lambda r: r["unix_time"])
    selected = {}
    # Warmup and follow-up requests also pass the gate. Select the probe
    # lookup immediately before disconnect, then its load and release.
    lookups = [r for r in rows if r["event"] == "gate_entered"
               and r.get("phase") == "lookup" and r["unix_time"] < disconnected]
    if not lookups:
        raise AssertionError("No lookup gate before cancellation")
    selected["lookup", "gate_entered"] = lookups[-1]
    pid, task_id = lookups[-1]["pid"], lookups[-1]["task_id"]
    for event, phase in (("gate_released", "lookup"), ("gate_entered", "load"),
                         ("gate_released", "load")):
        expected_task = (task_id if phase == "lookup" else
                         selected.get(("load", "gate_entered"), {}).get("task_id"))
        matches = [r for r in rows if r["event"] == event and r.get("phase") == phase
                   and r["pid"] == pid and (expected_task is None or r["task_id"] == expected_task)
                   and r["unix_time"] >= lookups[-1]["unix_time"]]
        if phase == "load" and event == "gate_entered":
            matches = matches[:1]
        if len(matches) != 1:
            raise AssertionError(f"Expected one probe {phase}/{event}, got {len(matches)}")
        selected[phase, event] = matches[0]
    lookup = selected["lookup", "gate_entered"]
    load = selected["load", "gate_entered"]
    released = selected["load", "gate_released"]
    assert lookup["unix_time"] < disconnected < load["unix_time"] < released["unix_time"]
    assert len(load["object_keys"]) == len(set(load["object_keys"])) == 17
    returned = [r for r in rows if r["event"] == "io_returned" and r.get("phase") == "load"
                and r["pid"] == load["pid"] and r["task_id"] == load["task_id"]]
    assert len(returned) == 1
    assert returned[0]["unix_time"] >= released["unix_time"]
    summary = dict(load_objects=17, actual_fs_coroutine_returned=True)
    if reclaim:
        owned = [r for r in rows if r["event"] == "reclaim_owned"]
        pending = [r for r in rows if r["event"] == "reclaim_pending"]
        completed = [r for r in rows if r["event"] == "reclaim_completed"]
        assert len(owned) == len(pending) == len(completed) == 1
        owner, waiting, done = owned[0], pending[0], completed[0]
        assert owner["prefetch_request_id"] >= 0  # Real controller, not L1-only handle -1.
        assert owner["token"] == waiting["token"] == done["token"]
        assert owner["request_id"] == waiting["request_id"] == done["request_id"]
        assert disconnected < owner["unix_time"] <= waiting["unix_time"] < load["unix_time"]
        assert done["unix_time"] >= released["unix_time"]
        assert done["released_objects"] == 17 and done["readers"] == 1
        assert len(done["object_keys"]) == 17
        assert set(done["object_keys"]) == set(load["object_keys"])
        summary.update(request_id=done["request_id"], prefetch_request_id=owner["prefetch_request_id"],
                       released_objects=17, keys_match_load=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reclaim", action="store_true")
    args = parser.parse_args()
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} occupied")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    gate = out / "events"
    gate.mkdir()
    disk = out / "kv-files"
    # Exclude inherited diagnostic toggles from another experiment.
    os.environ.pop("CACHEPILOT_LOOKUP_SERVER_RELEASE_CANCELLED", None)
    os.environ.update(CACHEPILOT_L2_GATE_DIR=str(gate),
                      CACHEPILOT_L2_RECLAIM="1" if args.reclaim else "0",
                      LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080")
    api, cache_api = "http://127.0.0.1:8000", "http://127.0.0.1:8080"
    cache_cmd = [sys.executable, str(ROOT / "scripts/l2_prefetch_gate.py"), "server",
                 "--host", "127.0.0.1", "--port", "5555", "--http-host", "127.0.0.1",
                 "--http-port", "8080", "--l1-size-gb", "16", "--l1-init-size-gb", "16",
                 "--eviction-policy", "LRU", "--enable-extra-logging", "--l2-adapter",
                 json.dumps(dict(type="fs", base_path=str(disk)))]
    engine_cmd = ["bash", str(ROOT / "scripts/serve.sh"), "immediate", "--enforce-eager"]
    cache = Service(cache_cmd, out / "lmcache-warmup.log")
    engine = Service(engine_cmd, out / "vllm-warmup.log")
    model, text = str(ROOT / "models/Qwen3-4B"), prompt()
    result = dict(candidate_reclaim=args.reclaim, connector="LMCacheMPConnector",
                  controlled_fs_io=True, eager=True, model=model,
                  prompt_sha256=hashlib.sha256(text.encode()).hexdigest(), passed=False)
    stream = None

    def snapshot(name):
        raw = requests.get(cache_api + "/status", timeout=10).json()
        write_json(out / f"{name}-status.json", raw)
        state = cache_state(cache_api)
        result[name] = state
        return state

    try:
        print("Starting warmup with real FS L2", flush=True)
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        reference = completion(api, model, text, 32)
        result["reference_usage"] = reference["usage"]
        wait_for(lambda: len(list(disk.glob("*.data"))) >= 17 and drained(cache_state(cache_api)), 30)
        engine.stop()
        cache.stop()
        result["disk_manifest"] = [dict(name=p.name, bytes=p.stat().st_size,
                                      sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                                   for p in sorted(disk.glob("*.data"))]
        print("Restarting both services: L1 empty, disk KV retained", flush=True)
        cache = Service(cache_cmd, out / "lmcache-probe.log")
        engine = Service(engine_cmd, out / "vllm-probe.log")
        cache.start(cache_api + "/status")
        engine.start(api + "/health")
        assert drained(snapshot("before"))
        (gate / "armed").touch()
        stream = open_stream("127.0.0.1", model, text)
        wait_for(lambda: any(e["event"] == "gate_entered" and e.get("phase") == "lookup"
                             for e in events(gate)), 15)
        wait_for(lambda: deferred_requests(get_text(api + "/metrics")) > 0, 15)
        stream.close()
        stream = None
        result["client_disconnected_unix"] = time.time()
        wait_for(lambda: deferred_requests(get_text(api + "/metrics")) == 0, 15)
        wait_for(lambda: any(e["event"] == "server_after" and e.get("method") == "end_session"
                             and e["unix_time"] > result["client_disconnected_unix"]
                             for e in events(gate)), 15)
        pending = snapshot("cancelled_lookup_held")
        assert pending["prefetch_in_flight"] == 1
        if args.reclaim:
            assert pending["abandoned_prefetch_jobs"] == 1
            assert pending["reclaim_completed_jobs"] == 0
        print("Cancelled while L2 lookup held; allowing lookup, holding actual file read", flush=True)
        (gate / "release-lookup").touch()
        wait_for(lambda: any(e["event"] == "gate_entered" and e.get("phase") == "load"
                             for e in events(gate)), 15)
        pending = snapshot("cancelled_load_held")
        assert pending["l1_write_locked"] == 17
        assert pending["l1_read_locked"] == 0 and pending["prefetch_load"] == 1
        if args.reclaim:
            assert pending["abandoned_prefetch_jobs"] == 1 and pending["reclaim_completed_jobs"] == 0
        time.sleep(1)
        assert snapshot("load_still_held")["l1_write_locked"] == 17
        (gate / "release-load").touch()
        wait_for(lambda: cache_state(cache_api)["prefetch_in_flight"] == 0, 15)
        if args.reclaim:
            wait_for(lambda: drained(cache_state(cache_api)), 15)
        time.sleep(1)
        after = snapshot("after_completion")
        result["resource_passed"] = drained(after)
        metrics_before = metric_summary(get_text(api + "/metrics"))
        follow_up = completion(api, model, text, 32)
        result["outputs_equal"] = reference["text"] == follow_up["text"]
        result["follow_up_usage"] = follow_up["usage"]
        result["external_hit_tokens"] = metric_summary(get_text(api + "/metrics"))["external_hit_tokens"] - metrics_before["external_hit_tokens"]
        snapshot("after_follow_up")
        assert result["outputs_equal"] and result["external_hit_tokens"] == 4352
        result["event_audit"] = audit_events(events(gate), result["client_disconnected_unix"], args.reclaim)
        engine.stop()
        final = snapshot("after_engine_stop")
        result["passed"] = (result["resource_passed"] and drained(result["after_follow_up"])
                            and drained(final) and not final["registered_gpu_ids"])
        if args.reclaim and not result["passed"]:
            raise AssertionError("Candidate failed resource checks")
        print(json.dumps({k: result[k] for k in ("passed", "resource_passed", "outputs_equal", "external_hit_tokens")}), flush=True)
    except Exception as exc:
        result.update(passed=False, error=repr(exc))
        raise
    finally:
        for phase in ("lookup", "load"):
            (gate / f"release-{phase}").touch()
        if stream is not None:
            stream.close()
        engine.stop()
        cache.stop()
        write_json(out / "result.json", result)


if __name__ == "__main__":
    main()
