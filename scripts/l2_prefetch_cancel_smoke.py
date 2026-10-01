"""Real FS L2 prefetch cancellation under controlled lookup/load timing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
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
    owned = ("owned_jobs", "owned_controller_jobs", "owned_transfers", "owned_deferred_contexts",
             "owned_failed", "owned_read_pins", "owned_store_batches")
    return state["is_healthy"] and all(state[key] == 0 for key in fields) and all(state.get(key, 0) == 0 for key in owned)


def audit_checked_release(rows, disconnected, retained_objects):
    releases = [r for r in rows if r["event"] == "reclaim_release_result"
                and r["unix_time"] > disconnected]
    completed = [r for r in rows if r["event"] == "reclaim_completed"
                 and r["unix_time"] > disconnected]
    assert len(completed) == 1
    done = completed[0]
    assert len(set(done["object_keys"])) == len(done["object_keys"]) == retained_objects
    assert done["released_objects"] == retained_objects
    if retained_objects == 0:
        assert not releases
        return dict(retained_objects=0, release_called=False)
    assert len(releases) == 1
    release = releases[0]
    assert release["request_id"] == done["request_id"] and release["token"] == done["token"]
    assert release["unix_time"] <= done["unix_time"]
    assert len(release["succeeded_keys"]) == retained_objects
    assert set(release["succeeded_keys"]) == set(done["object_keys"])
    assert not release["failed_keys"] and not release["errors"] and not release["notification_error"]
    return release


def audit_events(rows, disconnected, reclaim, retained_objects=17):
    """Reject a run that missed either barrier or reclaimed the wrong objects."""
    if any(row["event"] == "gate_timeout" for row in rows):
        raise AssertionError("Diagnostic barrier timed out")
    rows = sorted((r for r in rows if "unix_time" in r), key=lambda r: r["unix_time"])
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
        assert done["released_objects"] == retained_objects and done["readers"] == 1
        assert len(done["object_keys"]) == retained_objects
        assert set(done["object_keys"]) == set(load["object_keys"][:retained_objects])
        summary.update(request_id=done["request_id"], prefetch_request_id=owner["prefetch_request_id"],
                       released_objects=retained_objects, keys_match_load=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reclaim", action="store_true")
    parser.add_argument("--owned-service", action="store_true")
    parser.add_argument("--owned-shutdown", action="store_true")
    parser.add_argument("--checked-release", action="store_true")
    parser.add_argument("--truncate-index", type=int, choices=range(17))
    parser.add_argument("--crash-client", action="store_true")
    parser.add_argument("--observe-seconds", type=float, default=0)
    parser.add_argument("--shutdown-held-phase", choices=("lookup", "load"))
    parser.add_argument("--registration-grace-seconds", type=float)
    args = parser.parse_args()
    if args.owned_service and (args.reclaim or args.checked_release or args.shutdown_held_phase):
        parser.error("Owned service uses its own reclaim/shutdown protocol")
    if args.owned_shutdown and (not args.owned_service or args.crash_client or args.truncate_index is not None):
        parser.error("Owned shutdown requires owned service and a normal cancellation")
    if args.checked_release and not args.reclaim:
        parser.error("--checked-release requires --reclaim")
    if args.shutdown_held_phase and (args.crash_client or args.truncate_index is not None or args.observe_seconds):
        parser.error("Shutdown probe cannot combine with death, truncation, or TTL observation")
    if not 0 <= args.observe_seconds <= 1800:
        parser.error("--observe-seconds must be in [0, 1800]")
    if args.registration_grace_seconds is not None and not 120 <= args.registration_grace_seconds <= 3600:
        parser.error("Registration grace must be in [120, 3600]; reap timeout stays at default 120")
    for port in (8000, 8080, 5555):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} occupied")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    gate = out / "events"
    gate.mkdir()
    if args.owned_service:
        os.environ.update(CACHEPILOT_OWNED_SERVICE_DIR=str(gate), CACHEPILOT_OWNED_MP_DIR=str(gate))
    disk = out / "kv-files"
    # Exclude inherited diagnostic toggles from another experiment.
    os.environ.pop("CACHEPILOT_LOOKUP_SERVER_RELEASE_CANCELLED", None)
    os.environ.update(CACHEPILOT_L2_GATE_DIR=str(gate),
                      CACHEPILOT_L2_RECLAIM="1" if args.reclaim else "0",
                      CACHEPILOT_CHECKED_RELEASE="1" if args.checked_release else "0",
                      CACHEPILOT_L2_SHUTDOWN_TIMELINE="1" if args.shutdown_held_phase else "0",
                      LMCACHE_PORT="5555", LMCACHE_HTTP_PORT="8080")
    api, cache_api = "http://127.0.0.1:8000", "http://127.0.0.1:8080"
    cache_cmd = [sys.executable, str(ROOT / "scripts/l2_prefetch_gate.py"), "server",
                 "--host", "127.0.0.1", "--port", "5555", "--http-host", "127.0.0.1",
                 "--http-port", "8080", "--l1-size-gb", "16", "--l1-init-size-gb", "16",
                 "--eviction-policy", "LRU", "--enable-extra-logging", "--l2-adapter",
                 json.dumps(dict(type="fs", base_path=str(disk)))]
    if args.registration_grace_seconds is not None:
        cache_cmd += ["--worker-registration-grace-seconds", str(args.registration_grace_seconds)]
    engine_cmd = ["bash", str(ROOT / "scripts/serve.sh"), "owned-service" if args.owned_service else "immediate", "--enforce-eager"]
    cache = Service(cache_cmd, out / "lmcache-warmup.log")
    engine = Service(engine_cmd, out / "vllm-warmup.log")
    model, text = str(ROOT / "models/Qwen3-4B"), prompt()
    result = dict(candidate_reclaim=args.reclaim, owned_service=args.owned_service, connector="OwnedServiceConnector" if args.owned_service else "LMCacheMPConnector",
                  checked_release=args.checked_release,
                  controlled_fs_io=True, eager=True, model=model,
                  crash_client=args.crash_client, observe_seconds=args.observe_seconds,
                  shutdown_held_phase=args.shutdown_held_phase,
                  registration_grace_seconds=args.registration_grace_seconds,
                  usage_telemetry=os.environ.get("LMCACHE_TRACK_USAGE", "default"),
                  prompt_sha256=hashlib.sha256(text.encode()).hexdigest(), passed=False)
    stream = None
    truncated = None

    def snapshot(name):
        raw = requests.get(cache_api + "/status", timeout=10).json()
        write_json(out / f"{name}-status.json", raw)
        state = cache_state(cache_api)
        result[name] = state
        return state

    def shutdown_held():
        before = snapshot("before_shutdown")
        started = time.monotonic()
        cache.process.terminate()
        forced = False
        try:
            # HTTP shutdown flushes telemetry before engine.close; allow that
            # bounded overhead while staying below the 60s FS barrier deadline.
            cache.process.wait(timeout=40)
        except subprocess.TimeoutExpired:
            forced = True
            os.killpg(cache.process.pid, signal.SIGKILL)
            cache.process.wait(timeout=10)
        rows = events(gate)
        cutoff = result["client_disconnected_unix"]
        closing = [r for r in rows if r["event"].startswith("close_") and r["unix_time"] > cutoff]
        result["shutdown"] = dict(forced_kill=forced, returncode=cache.process.returncode,
            elapsed_seconds=time.monotonic() - started, events=closing,
            note="Held async FS coroutine; not a blocked native filesystem syscall")
        result["resource_passed"] = drained(before)
        result["diagnostic_completed"] = not forced and all(any(
            r["event"] == "close_return" and r["component"] == component for r in closing)
            for component in ("LookupModule", "StorageManager", "PrefetchController", "FSL2Adapter"))
        result["passed"] = result["resource_passed"] and result["diagnostic_completed"]
        print(json.dumps(result["shutdown"]), flush=True)
        # Process teardown reclaims address space, but is not proof that the
        # per-job lifecycle reached its terminal state before teardown.
        assert not any(r["event"] == "gate_timeout" for r in rows)

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
        if args.crash_client:
            # Kill only the process group created by this experiment. This
            # prevents normal request_finished/END_SESSION cleanup.
            os.killpg(engine.process.pid, signal.SIGKILL)
            engine.process.wait(timeout=10)
        stream.close()
        stream = None
        result["client_disconnected_unix"] = time.time()
        if not args.crash_client:
            wait_for(lambda: deferred_requests(get_text(api + "/metrics")) == 0, 15)
            wait_for(lambda: any(e["event"] == "server_after" and e.get("method") == "end_session"
                                 and e["unix_time"] > result["client_disconnected_unix"]
                                 for e in events(gate)), 15)
        pending = snapshot("cancelled_lookup_held")
        assert pending["prefetch_in_flight"] == 1
        if args.reclaim and not args.crash_client:
            assert pending["abandoned_prefetch_jobs"] == 1
            assert pending["reclaim_completed_jobs"] == 0
        if args.shutdown_held_phase == "lookup":
            shutdown_held()
            return
        print("Cancelled while L2 lookup held; allowing lookup, holding actual file read", flush=True)
        (gate / "release-lookup").touch()
        wait_for(lambda: any(e["event"] == "gate_entered" and e.get("phase") == "load"
                             for e in events(gate)), 15)
        pending = snapshot("cancelled_load_held")
        assert pending["l1_write_locked"] == 17
        assert pending["l1_read_locked"] == 0 and pending["prefetch_load"] == 1
        if args.reclaim and not args.crash_client:
            assert pending["abandoned_prefetch_jobs"] == 1 and pending["reclaim_completed_jobs"] == 0
        time.sleep(1)
        assert snapshot("load_still_held")["l1_write_locked"] == 17
        if args.owned_shutdown:
            (gate / "probe-close").touch()
            wait_for(lambda: (gate / "close-refused").exists(), 20)
            held = snapshot("close_refused_load_held")
            assert held["l1_write_locked"] == 17 and held["prefetch_load"] == 1
            (gate / "release-load").touch()
            wait_for(lambda: drained(cache_state(cache_api)), 20)
            engine.stop()
            final = snapshot("after_engine_stop")
            assert not final["registered_gpu_ids"] and drained(final)
            cache.stop()
            rows = events(gate)
            assert any(r["event"] == "owned_shutdown_blocked" for r in rows)
            assert any(r["event"] == "owned_shutdown_drained" and r["unix_time"] > result["client_disconnected_unix"] for r in rows)
            assert not any(r["event"] == "gate_timeout" for r in rows)
            result.update(passed=True, resource_passed=True, shutdown_blocked_with_live_writers=True,
                          shutdown_drained_after_io=True)
            return
        if args.shutdown_held_phase == "load":
            shutdown_held()
            return
        if args.truncate_index is not None:
            load = next(e for e in events(gate) if e["event"] == "gate_entered"
                        and e.get("phase") == "load")
            target = Path(load["paths"][args.truncate_index]).resolve()
            if target.parent != disk.resolve():
                raise AssertionError("Truncation target outside experiment KV directory")
            saved = target.read_bytes()
            truncated = (target, saved)
            with target.open("r+b") as handle:
                handle.truncate(len(saved) // 2)
            result["short_read_injection"] = dict(index=args.truncate_index,
                filename=target.name, before_bytes=len(saved), after_bytes=target.stat().st_size,
                unix_time=time.time())
        (gate / "release-load").touch()
        wait_for(lambda: cache_state(cache_api)["prefetch_in_flight"] == 0, 15)
        if (args.reclaim or args.owned_service) and not args.crash_client:
            wait_for(lambda: drained(cache_state(cache_api)), 15)
        time.sleep(1)
        after = snapshot("after_completion")
        result["resource_passed"] = drained(after)
        if args.observe_seconds:
            started = time.monotonic()
            observations = []
            while time.monotonic() - started < args.observe_seconds:
                time.sleep(min(15, max(0, args.observe_seconds - (time.monotonic() - started))))
                elapsed = time.monotonic() - started
                state = snapshot(f"observe-{len(observations):03d}")
                observations.append(dict(elapsed_seconds=elapsed, state=state))
                write_json(out / "observations.json", observations)
                print(json.dumps(dict(elapsed_seconds=round(elapsed),
                    read_locks=state["l1_read_locked"], jobs=state["active_prefetch_jobs"],
                    sessions=state["active_sessions"], result=state["completed_results_count"])), flush=True)
            result["observed_final"] = observations[-1]["state"]
            if args.owned_service:
                result["resource_passed"] = drained(result["observed_final"])
        if args.crash_client:
            assert not any(e["event"] == "server_after" and e.get("method") == "end_session"
                           and e["unix_time"] > result["client_disconnected_unix"] for e in events(gate))
            result["no_end_session_observed"] = True
            engine = Service(engine_cmd, out / "vllm-recovery.log")
            engine.start(api + "/health")
        if truncated is not None:
            target, saved = truncated
            target.write_bytes(saved)
            result["short_read_injection"]["restored_sha256"] = hashlib.sha256(saved).hexdigest()
            truncated = None
        metrics_before = metric_summary(get_text(api + "/metrics"))
        follow_up = completion(api, model, text, 32)
        result["outputs_equal"] = reference["text"] == follow_up["text"]
        result["follow_up_usage"] = follow_up["usage"]
        result["external_hit_tokens"] = metric_summary(get_text(api + "/metrics"))["external_hit_tokens"] - metrics_before["external_hit_tokens"]
        snapshot("after_follow_up")
        assert result["outputs_equal"] and result["external_hit_tokens"] == 4352
        retained = 17 if args.truncate_index is None else args.truncate_index
        result["event_audit"] = audit_events(events(gate), result["client_disconnected_unix"],
                                            args.reclaim and not args.crash_client, retained)
        if args.owned_service:
            rows = events(gate)
            done = [r for r in rows if r["event"] == "lookup_reclaimed" and r["unix_time"] > result["client_disconnected_unix"]]
            assert done
            request_id = done[0]["request_id"]
            acquired = [r for r in rows if r["event"] == "reservation_acquired" and r["request_id"] == request_id]
            released = [r for r in rows if r["event"] == "reservation_released" and r["request_id"] == request_id]
            identity = lambda r: (r["lock_id"], r["epoch"], r["serial"])
            assert acquired and len(acquired) == len(released)
            assert len(set(map(identity, released))) == len(released)
            assert set(map(identity, acquired)) == set(map(identity, released))
            collected = [r for r in rows if r["event"] == "prefetch_collected"
                         and r["request_id"] == request_id]
            assert len(collected) == 1 and collected[0]["objects"] == retained
            returned = [r["unix_time"] for r in rows if r["event"] == "io_returned" and r.get("phase") == "load" and r["unix_time"] > result["client_disconnected_unix"]]
            assert all(r["unix_time"] >= min(returned) for r in released)
            result["owned_audit"] = dict(original_tokens=len(acquired), retained_objects=retained,
                                        exactly_once=True, release_after_io=True)
        if args.checked_release and not args.crash_client:
            result["checked_release_audit"] = audit_checked_release(
                events(gate), result["client_disconnected_unix"], retained)
        engine.stop()
        final = snapshot("after_engine_stop")
        result["passed"] = (result["resource_passed"] and drained(result["after_follow_up"])
                            and drained(final) and not final["registered_gpu_ids"])
        if (args.reclaim or args.owned_service) and not result["passed"]:
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
        if truncated is not None:
            target, saved = truncated
            target.write_bytes(saved)
        write_json(out / "result.json", result)


if __name__ == "__main__":
    main()
