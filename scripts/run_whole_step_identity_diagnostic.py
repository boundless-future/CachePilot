"""One fixed-trace identity/cache-path diagnosis; timing is not an outcome."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(1, str(ROOT / "scripts"))

from cachepilot.config import WholeStepConfig
from run_arrival_matrix import digest
from run_fixed_baselines import KV_BYTES, read
import run_protection_ablation as ablation
from run_real_structure_pilot import TRACE_SHA, validate_inputs
from analyze_whole_step_identity import analyze_cell
from validate_environment import write_json


LABELS = ("zero", "legacy-two", "filtered-two")
MODES = {"zero": "zero", "legacy-two": "two", "filtered-two": "two"}
NATIVE_DEADLINE_SECONDS = 0.5


def configuration(label):
    config = WholeStepConfig(max_chunks=0 if label == "zero" else 2).kv_transfer_config()
    config.update(kv_connector="WholeStepIdentityConnector",
        kv_connector_module_path="scripts.whole_step_identity_connector")
    if label == "filtered-two":
        config["kv_connector_extra_config"]["cachepilot.skip_ineligible"] = True
    return config


def run(args):
    from replay_trace import replay as original_replay
    import validate_environment as env

    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        raise ValueError("Future timezone-aware cutoff required")
    model = ROOT / "models/Qwen3-4B"
    provenance = validate_inputs(args.input / "built", args.input / "source",
                                 args.input / "catalogue.json", model)
    trace = read(args.input / "built/trace.json")
    if digest(trace) != TRACE_SHA:
        raise ValueError("Original fixed trace required")
    ablation.ports_free()
    diagnostic = getattr(args, "diagnostic", "identity")
    window_diagnostic = diagnostic in ("source1-window", "source1-deadline")
    labels = ("zero",) if window_diagnostic else LABELS
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = ablation.source_sha()
    plans = {label: configuration(label) for label in labels}
    if window_diagnostic:
        plans["zero"].update(kv_connector="Source1WindowConnector",
            kv_connector_module_path="scripts.source1_window_connector")
    if diagnostic == "source1-deadline":
        plans["zero"]["kv_connector_extra_config"][
            "lmcache.mp.lazy_offload_max_deferral_seconds"] = NATIVE_DEADLINE_SECONDS
    write_json(out / "plan.json", dict(stage=diagnostic if window_diagnostic else "identity-diagnostic", labels=list(labels),
        signature=dict(source_sha256=sources, trace_sha256=TRACE_SHA,
                       kv_cache_bytes=KV_BYTES, configs=plans,
                       stable_id_scheme="cachepilot-trace-{event_id:04d}",
                       request_id_randomization_disabled=True),
        provenance=provenance,
        scope="Identity and cache-path mechanism only; excludes timing conclusions"))
    shutil.copytree(args.input, out / "input")
    write_json(out / "trace.json", trace)
    with tarfile.open(out / "frozen-source.tar.gz", "w:gz") as archive:
        for name in sources:
            archive.add(ROOT / name, arcname=name)

    async def replay_with_ids(input_trace, base_url, request_model, mode="open", limit=64):
        ids = {event["event_id"]: f"cachepilot-trace-{event['event_id']:04d}"
               for event in input_trace["events"]}
        return await original_replay(input_trace, base_url, request_model, mode, limit,
                                     request_ids=ids)

    original_service = ablation.Service
    original_replay_binding = ablation.replay
    current = None

    class DiagnosticService(env.Service):
        def start(self, health, timeout=900):
            cell = out / f"{current}-r0"
            os.environ["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
            os.environ["CACHEPILOT_HANDOFF_DIR"] = str(cell / "handoffs")
            os.environ["CACHEPILOT_OWNED_EVENT_LOG"] = "1"
            if self.log.name == "lmcache.log":
                self.command = [sys.executable, str(ROOT / "scripts/generation_service_observer.py"),
                    "server", "--host", "127.0.0.1", "--port", "5555",
                    "--http-host", "127.0.0.1", "--http-port", "8080",
                    "--l1-size-gb", "16", "--l1-init-size-gb", "16", "--eviction-policy", "LRU"]
            else:
                config = plans[current]
                self.command[self.command.index("--kv-transfer-config") + 1] = json.dumps(config)
                os.environ["CACHEPILOT_GENERATION_SUMMARY_PATH"] = str(cell / "generation-summary.json")
                os.environ["VLLM_DISABLE_REQUEST_ID_RANDOMIZATION"] = "1"
                write_json(cell / "config.json", config)
            write_json(cell / (self.log.stem + "-launch.json"), self.command)
            return super().start(health, timeout)

    ablation.Service = DiagnosticService
    ablation.replay = replay_with_ids
    completed = []
    try:
        for label in labels:
            if datetime.now(timezone.utc) >= cutoff:
                write_json(out / "stopped.json", dict(next_cell=f"{label}-r0", reason="cutoff"))
                break
            if ablation.source_sha() != sources:
                raise RuntimeError("Source changed after diagnostic freeze")
            ablation.ports_free()
            current = label
            cell = out / f"{label}-r0"
            print("START " + cell.name, flush=True)
            ablation.run_cell(cell, MODES[label], 0, trace, str(model), KV_BYTES)
            diagnosis = analyze_cell(cell, label)
            write_json(cell / "identity-analysis.json", diagnosis)
            if not diagnosis["passed"]:
                raise RuntimeError(f"Identity/cache-path audit failed: {diagnosis['failures']}")
            if window_diagnostic:
                from analyze_source1_window import audit as window_audit
                report = window_audit(cell)
                write_json(cell / "source1-window-analysis.json", report)
                if not report["passed"]:
                    raise RuntimeError("Source1 window observations missing")
            completed.append(label)
            print("DONE " + cell.name, flush=True)
        write_json(out / "identity-summary.json", dict(completed=completed,
            cells=[read(out / f"{label}-r0/identity-analysis.json") for label in completed],
            scope="Mechanism diagnosis only; no latency/performance conclusion"))
        if window_diagnostic and tuple(completed) == labels:
            from analyze_source1_window import audit_run
            report = audit_run(out)
            audit_name = "source1-deadline-audit.json" if diagnostic == "source1-deadline" else "source1-window-audit.json"
            write_json(out / audit_name, report)
            if not report["complete"]:
                raise RuntimeError("Independent window/resource audit failed")
        elif tuple(completed) == LABELS:
            from audit_whole_step_identity import audit
            report = audit(out)
            write_json(out / "identity-audit.json", report)
            if not report["complete"]:
                raise RuntimeError("Independent identity/resource audit failed")
    except BaseException as exc:
        write_json(out / "stopped.json", dict(cell=current, completed=completed, error=repr(exc)))
        raise
    finally:
        ablation.Service = original_service
        ablation.replay = original_replay_binding
        os.environ.pop("VLLM_DISABLE_REQUEST_ID_RANDOMIZATION", None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-starting-at", required=True)
    parser.add_argument("--diagnostic", choices=("identity", "source1-window", "source1-deadline"), default="identity")
    args = parser.parse_args()
    run(args)
