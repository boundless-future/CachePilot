"""Real EngineCore host-fault exit evidence, separate from normal GPU admission."""
import argparse
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import protection_zero_smoke as smoke
    from run_protection_ablation import ports_free, source_sha
    from validate_environment import Service, ROOT, write_json
    from audit_generation_pilot import rows
    ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    cell = out / 'after-commit'
    write_json(out / 'plan.json', dict(stage='generation-protection-host-fault',
        source_sha256=source_sha(), mode='one', fault='after_commit_before_handoff',
        workload='unchanged bounded smoke', performance=False, driver_fault=False,
        expected='failed request, autonomous engine exit, unresolved snapshot; not normal drain'))

    class FaultService(Service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_GENERATION_FAULT'] = 'after_commit'
            os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(cell / 'generation.json')
            if self.log.name == 'vllm.log':
                i = self.command.index('--kv-transfer-config') + 1
                config = json.loads(self.command[i])
                config.update(kv_connector='GenerationProtectionFaultProbeConnector',
                    kv_connector_module_path='scripts.generation_protection_fault_probe')
                config['kv_connector_extra_config'].update({
                    'cachepilot.generation_ledger': True, 'cachepilot.generation_protection': True})
                self.command[i] = json.dumps(config)
                write_json(cell / 'config.json', config)
            return super().start(health, timeout)

        def stop(self):
            target = cell / 'before-harness-stop.json'
            if self.log.name == 'vllm.log' and self.process and not target.exists():
                events = rows(cell / 'events', 'handoff-*.jsonl')
                injected = [r for r in events if r['event'] == 'generation_fault_injected']
                if injected:
                    deadline = time.monotonic() + 20
                    while self.process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.2)
                pids = sorted({r['pid'] for r in injected})
                states = {}
                for pid in pids:
                    stat = Path(f'/proc/{pid}/stat')
                    states[str(pid)] = stat.read_text().rsplit(')', 1)[1].split()[0] if stat.exists() else 'absent'
                write_json(target, dict(injections=len(injected), api_returncode=self.process.poll(),
                    engine_states=states, snapshot_exists=(cell / 'generation.json').exists(),
                    before_harness_signal=True))
            return super().stop()

    smoke.Service = FaultService
    caught = None
    try:
        smoke.run(cell, 'one', bounded_workload=True)
    except Exception as exc:
        caught = repr(exc)
    events = rows(cell / 'events', 'handoff-*.jsonl')
    injected = [r for r in events if r['event'] == 'generation_fault_injected']
    snapshot = json.loads((cell / 'generation.json').read_text()) if (cell / 'generation.json').exists() else {}
    stopped = json.loads((cell / 'before-harness-stop.json').read_text())
    targets = [t for event in injected for t in event['targets']]
    wires = {t['wire_id'] for t in targets}
    checks = dict(exact_injection=len(injected) == 1 and bool(targets),
        native_owned_at_fault=bool(targets) and all(t['native_inflight'] and all(n > 0 for n in t['ref_counts']) for t in targets),
        request_failed=caught is not None,
        api_exited_without_harness_signal=stopped['api_returncode'] is not None,
        engine_exited=bool(stopped['engine_states']) and all(s in ('absent', 'Z') for s in stopped['engine_states'].values()),
        failed_snapshot=bool(snapshot.get('protection_poisoned')) and snapshot.get('shutdown_complete') is False,
        retains_unresolved_pins=snapshot.get('protected_inflight_blocks', 0) > 0 and snapshot.get('inflight_store', 0) > 0,
        no_target_end=not any(r['event'] == 'generation_end_submitted' and r['wire_id'] in wires for r in events),
        no_target_rollback=not any(r['event'] == 'protection_rollback' and r['wire_id'] in wires and r['monotonic_ns'] > injected[0]['monotonic_ns'] for r in events) if injected else False)
    write_json(out / 'validation.json', dict(passed=all(checks.values()), checks=checks, caught=caught,
        scope='injected scheduler host exception; not DMA fault or automatic recovery'))
    ports_free()
    print(json.dumps(checks), flush=True)
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == '__main__':
    main()
