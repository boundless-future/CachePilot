"""Separate capacity benchmark for a frozen, opt-in idle-finalization stack.

Reuse the original measurement path; no handoff JSONL, KV probe or diagnostic
service wrapper. Pilot and formal repetitions have independent directories.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def strict_idle_state(state):
    return (state.get('active_sessions') == 0 and
            state.get('storage_manager', {}).get('l1_manager', {}).get('temporary_count') == 0)


def check_pilot(pilot, signature):
    plan = json.loads((pilot / 'plan.json').read_text(encoding='utf-8'))
    assert plan['stage'] == 'pilot' and plan['signature'] == signature, 'Pilot configuration/source mismatch'
    for mode in ('zero', 'one', 'two'):
        cell = pilot / f'{mode}-r0'
        result = json.loads((cell / 'result.json').read_text(encoding='utf-8'))
        check = json.loads((cell / 'idle-validation.json').read_text(encoding='utf-8'))
        assert result['passed'] and check['passed'], ('Pilot failed', mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--stage', choices=('pilot', 'formal'), required=True)
    parser.add_argument('--pilot', type=Path)
    parser.add_argument('--stop-starting-at', help='ISO UTC cutoff; finish the active cell, then stop')
    args = parser.parse_args()
    if (args.stage == 'formal') != (args.pilot is not None):
        parser.error('Only formal runs require --pilot')
    cutoff = datetime.fromisoformat(args.stop_starting_at) if args.stop_starting_at else None
    if cutoff is not None and cutoff.utcoffset() is None:
        parser.error('Cutoff must include a timezone')
    import run_protection_ablation as ablation
    from validate_environment import Service, write_json
    trace = json.loads(args.trace.read_text(encoding='utf-8'))
    ablation.validate_trace(trace)
    capacity, grace = 2684354560, 15.0
    signature = dict(trace_sha256=hashlib.sha256(json.dumps(trace,sort_keys=True).encode()).hexdigest(),
                     kv_cache_bytes=capacity, idle_grace_seconds=grace,
                     source_sha256=ablation.source_sha(), gate='idle-capacity-v1')
    if args.pilot is not None:
        check_pilot(args.pilot, signature)
    ablation.ports_free()
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / 'plan.json', dict(stage=args.stage, signature=signature,
        repeats=1 if args.stage == 'pilot' else 3,
        policy='Same IdleFinalizeConnector for zero/one/two; default config unchanged',
        diagnostic_logs=False, historical_capacity_results_unchanged=True))

    class IdleService(Service):
        def start(self, health, timeout=900):
            if self.log.name == 'vllm.log':
                index = self.command.index('--kv-transfer-config') + 1
                config = json.loads(self.command[index])
                config['kv_connector'] = 'IdleFinalizeConnector'
                config['kv_connector_module_path'] = 'scripts.idle_finalize_connector'
                config['kv_connector_extra_config'].update({
                    'cachepilot.idle_finalize': True,
                    'cachepilot.idle_finalize_grace_seconds': grace})
                self.command[index] = json.dumps(config)
                write_json(self.log.parent / 'config.json', config)
            return super().start(health, timeout)

    ablation.Service = IdleService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle_state(state)
    modes = ['zero', 'one', 'two']
    summary = []
    for repeat in range(1 if args.stage == 'pilot' else 3):
        for mode in modes[repeat % 3:] + modes[:repeat % 3]:
            if cutoff is not None and datetime.now(timezone.utc) >= cutoff:
                write_json(args.output / 'stopped-at-cutoff.json', dict(
                    completed=len(summary), next_cell=f'{mode}-r{repeat}',
                    cutoff=args.stop_starting_at))
                return
            assert ablation.source_sha() == signature['source_sha256'], 'Sources changed during experiment'
            cell = args.output / f'{mode}-r{repeat}'
            print(f'START {cell.name}', flush=True)
            result = ablation.run_cell(cell, mode, repeat, trace,
                                      str(ablation.ROOT / 'models/Qwen3-4B'), capacity)
            # The stricter gate is applied during the original live drain too.
            checked = dict(passed=bool(result['passed'] and
                strict_idle_state(result['status_before_engine_stop']) and
                strict_idle_state(result['status_after_engine_stop'])),
                signature=signature, idle_drop_count=result['protection_summary']['events'].get('idle_pending_dropped',0),
                diagnostic_logs=False)
            write_json(cell / 'idle-validation.json', checked)
            assert checked['passed'], 'Strict idle resource gate failed'
            summary.append(dict(cell=cell.name, passed=True,
                                idle_drop_count=checked['idle_drop_count']))
            write_json(args.output / 'completed.json', summary)
            print(f'DONE {cell.name}', flush=True)


if __name__ == '__main__':
    main()
