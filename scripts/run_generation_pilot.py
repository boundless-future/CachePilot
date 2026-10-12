"""Independent diagnostic GPU pilot; never relabels original failed cells.

Fixed zero protection, same long-prefill trace/model/KV capacity/default lazy
policy. Adds ledger connector and read-only service observer. Event logging
is intentionally enabled, so these timings are not formal performance data.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

TRACE_SHA = 'd15108f305834554a7cd3cbe7da5103ee3a8348e587594300db5bf37d29e3cd0'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prerequisite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stop-starting-at', required=True)
    args = parser.parse_args()
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        parser.error('Future timezone-aware cutoff required')
    import run_protection_ablation as ablation
    from run_arrival_matrix import digest
    from run_fixed_baselines import read, configure, CASES, KV_BYTES, strict_idle, token_gate
    from validate_environment import Service, write_json
    from audit_replay_measurements import audit_cell as measurement_audit
    from audit_generation_pilot import audit_cell as generation_audit
    prior = read(args.prerequisite / 'h2p5-d64-r0/result.json')
    trace = read(args.prerequisite / 'trace.json')
    if digest(trace) != TRACE_SHA or prior['passed'] or not any(
        'Idle pending differs from current wire identity' in row['text']
            for row in prior.get('service_errors', [])):
        raise ValueError('Requires preserved original long-prefill failure')
    frozen = read(args.prerequisite / 'plan.json')['signature']['source_sha256']
    sources = ablation.source_sha()
    for path in ('scripts/idle_finalize.py', 'scripts/owned_service_protocol.py',
                 'scripts/owned_service.py', 'scripts/protection_connector.py'):
        if sources[path] != frozen[path]:
            raise ValueError('Original guard/ownership/handoff sources changed: ' + path)
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'plan.json', dict(stage='independent-generation-diagnostic',
        trace_sha256=TRACE_SHA, source_sha256=sources, kv_cache_bytes=KV_BYTES,
        model=str(ablation.ROOT / 'models/Qwen3-4B'), protection_chunks=0,
        prerequisite=str(args.prerequisite), cutoff=args.stop_starting_at,
        base_case=CASES['h2p5-d64'], formal=False))
    cell = out / 'zero-r0'

    class PilotService(Service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '1'
            os.environ['CACHEPILOT_HANDOFF_DIR'] = str(cell / 'handoffs')
            if self.log.name == 'lmcache.log':
                self.command = [os.sys.executable,
                    str(ablation.ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555',
                    '--http-host', '127.0.0.1', '--http-port', '8080',
                    '--l1-size-gb', '16', '--l1-init-size-gb', '16',
                    '--eviction-policy', 'LRU', '--enable-extra-logging']
            elif self.log.name == 'vllm.log':
                i = self.command.index('--kv-transfer-config') + 1
                config = configure(json.loads(self.command[i]), CASES['h2p5-d64'])
                config.update(kv_connector='GenerationLedgerConnector',
                    kv_connector_module_path='scripts.generation_ledger_connector')
                config['kv_connector_extra_config']['cachepilot.generation_ledger'] = True
                self.command[i] = json.dumps(config)
                write_json(cell / 'config.json', config)
            return super().start(health, timeout)

    ablation.Service = PilotService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle(state)
    caught = None
    try:
        ablation.run_cell(cell, 'zero', 0, trace,
            str(ablation.ROOT / 'models/Qwen3-4B'), KV_BYTES)
    except Exception as exc:
        caught = repr(exc)
    result = read(cell / 'result.json')
    audit = generation_audit(cell)
    checks = dict(replay_resources=result['passed'], measurements=measurement_audit(cell),
                  generation=audit, tokens=token_gate(cell, trace))
    passed = (checks['replay_resources'] and checks['measurements']['passed']
              and audit['passed'] and checks['tokens'])
    write_json(out / 'validation.json', dict(passed=passed, checks=checks, caught=caught))
    ablation.ports_free()
    print(json.dumps(dict(passed=passed, generation=audit, caught=caught)), flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
