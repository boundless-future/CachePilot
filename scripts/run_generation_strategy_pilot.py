"""Development-only, matched-parameter generation/protection performance pilot."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_fixed_baselines import configure, read, KV_BYTES, strict_idle, select_fixed, validate_completed, CASES as TUNING_CASES
from run_generation_matrix import extra_checks
from run_arrival_matrix import digest

TRACE_SHA = '49f2c9cb0b2faba83806a53727ef2280fa2bf38351154041f18847c3b0b056af'
CASES = {
    'default-zero': dict(kind='lazy', horizon=2.5, drain=64, mode='zero'),
    'selected-zero': dict(kind='lazy', horizon=5.0, drain=1, mode='zero'),
    'selected-one': dict(kind='lazy', horizon=5.0, drain=1, mode='one'),
    'selected-two': dict(kind='lazy', horizon=5.0, drain=1, mode='two'),
}


def strategy_config(original, case):
    result = configure(original, case)
    result.update(kv_connector='GenerationProtectionConnector',
                  kv_connector_module_path='scripts.generation_protection_connector')
    result['kv_connector_extra_config'].update({
        'cachepilot.generation_ledger': True, 'cachepilot.generation_protection': True})
    return result


def audit_cell(cell, case, signature, trace):
    import run_protection_ablation as ablation
    from run_reference_baselines import resource_gate
    result = read(cell / 'result.json')
    manifest = read(cell / 'run-manifest.json')
    snapshot = read(cell / 'generation-summary.json')
    summary = ablation.merge_protection_summary(cell / 'protection-summary')
    logs = ablation.service_error_report(cell, read(cell / 'shutdown-context.json'))
    checks = dict(extra_checks(cell, case, result, trace),
        result_passed=result['passed'], resources=resource_gate('lazy', result),
        config=read(cell / 'config.json') == strategy_config(read(ablation.ROOT / 'configs/protection-zero.json'), case),
        source=manifest['source_sha256'] == signature['source_sha256'],
        trace=digest(read(cell / 'trace.json')) == digest(trace) == manifest['trace_sha256'] == signature['trace_sha256'],
        budget=manifest['kv_cache_bytes'] == KV_BYTES,
        mode=manifest['mode'] == case['mode'] and result['mode'] == case['mode'],
        healthy=snapshot.get('protection_poisoned') is None and not snapshot.get('fault_error') and not snapshot.get('shutdown_error'),
        receipts=summary['committed_batches'] == summary['receipts'] and summary['failed_receipts'] == 0,
        coverage=case['mode'] == 'zero' or summary['committed_batches'] > 0,
        bounded=summary['peak_pinned_blocks'] <= {'zero': 0, 'one': 16, 'two': 32}[case['mode']],
        protection_closed=summary['shutdown_closed'],
        h2d=result['h2d_bytes'] > 0,
        fatal_logs=not logs['fatal_errors'])
    return dict(passed=all(checks.values()), checks=checks,
        ttft_p95_ms=result['ttft_ms']['p95'], h2d_bytes=result['h2d_bytes'], d2h_bytes=result['d2h_bytes'],
        committed=summary['committed_batches'], receipts=summary['receipts'],
        peak_pinned_blocks=summary['peak_pinned_blocks'], fallbacks=summary['fallbacks'],
        shutdown_teardown_errors=logs['shutdown_teardown_errors'])


def collect(directory):
    plan, trace = read(directory / 'plan.json'), read(directory / 'trace.json')
    if plan['cases'] != CASES or digest(trace) != TRACE_SHA:
        raise ValueError('Frozen pilot cases or development trace changed')
    rows = []
    for name, case in CASES.items():
        try:
            row = audit_cell(directory / (name + '-r0'), case, plan['signature'], trace)
        except (OSError, KeyError, ValueError, TypeError) as exc:
            row = dict(passed=False, error=repr(exc))
        rows.append(dict(case=name, **row))
    complete = all(r['passed'] for r in rows)
    comparison = {}
    if complete:
        baseline = rows[1]['ttft_p95_ms']
        comparison = {r['case']: 100 * (r['ttft_p95_ms'] / baseline - 1) for r in rows}
    return dict(matrix_complete=complete, rows=rows, relative_selected_zero_pct=comparison,
                scope='Single development pilot; no formal, significance or generalization claim')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tuning', type=Path, required=True)
    p.add_argument('--diagnostic', type=Path, required=True)
    p.add_argument('--runtime-diagnostic', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    import run_protection_ablation as ablation
    import validate_environment as env
    if Path(ablation.__file__).resolve() != Path(__file__).with_name('run_protection_ablation.py').resolve():
        raise RuntimeError('Measurement helper imported outside frozen scripts directory')
    from scripts.audit_generation_protection_smoke import audit_cell as admission
    tuning = read(args.tuning / 'plan.json')
    selection = select_fixed(validate_completed(args.tuning, tuning['signature'], 'formal', TUNING_CASES, 3))
    if selection['selected'] != 'h5p0-d1':
        raise ValueError('Historical frozen selection changed')
    trace = read(args.tuning / 'trace.json')
    if digest(trace) != TRACE_SHA:
        raise ValueError('Original development trace required')
    ablation.validate_trace(trace)
    sources = ablation.source_sha()
    diagnostic_sources = read(args.runtime_diagnostic / 'plan.json')['source_sha256']
    # Only harness/audit additions may differ from the last successful normal diagnostic.
    runtime_paths = [path for path in diagnostic_sources if path.startswith('configs/') or
        (path.startswith('scripts/') and ('connector' in path or path.rsplit('/', 1)[-1].startswith(
            ('generation_', 'bounded_', 'owned_', 'idle_', 'protection_handoff'))))]
    if any(sources.get(path) != diagnostic_sources[path] for path in runtime_paths):
        raise ValueError('Runtime differs from admitted normal diagnostic')
    admissions = {mode: admission(args.diagnostic / mode) for mode in ('zero', 'one', 'two')}
    from scripts.audit_generation_shared import audit as shared_admission
    admissions['current_shared_preemption'] = shared_admission(args.runtime_diagnostic / 'two', preemption=True)
    if not all(a['passed'] for a in admissions.values()):
        raise ValueError('Normal-path admission incomplete')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    signature = dict(source_sha256=sources, trace_sha256=TRACE_SHA, kv_cache_bytes=KV_BYTES,
                     cases=CASES, gate='generation-strategy-development-pilot-v1', hotpath_diagnostics=False)
    env.write_json(out / 'trace.json', trace)
    env.write_json(out / 'plan.json', dict(stage='pilot', repeats=1, cases=CASES, signature=signature,
        selection=selection, tuning=str(args.tuning), diagnostic=str(args.diagnostic), runtime_diagnostic=str(args.runtime_diagnostic),
        admissions=admissions, admission_runtime_paths=runtime_paths))
    current = None
    class StrategyService(env.Service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '0'
            if self.log.name == 'lmcache.log':
                self.command = [sys.executable, str(ablation.ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555', '--http-host', '127.0.0.1',
                    '--http-port', '8080', '--l1-size-gb', '16', '--l1-init-size-gb', '16', '--eviction-policy', 'LRU']
            elif self.log.name == 'vllm.log':
                i = self.command.index('--kv-transfer-config') + 1
                config = strategy_config(json.loads(self.command[i]), current)
                self.command[i] = json.dumps(config)
                env.write_json(self.log.parent / 'config.json', config)
                env.write_json(self.log.parent / 'launch-command.json', self.command)
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(self.log.parent / 'generation-summary.json')
            return super().start(health, timeout)
    ablation.Service = StrategyService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle(state)
    for name, current in CASES.items():
        if ablation.source_sha() != sources:
            raise RuntimeError('Source changed during pilot')
        ablation.ports_free()
        cell = out / (name + '-r0')
        print('START ' + cell.name, flush=True)
        try:
            ablation.run_cell(cell, current['mode'], 0, trace, str(ablation.ROOT / 'models/Qwen3-4B'), KV_BYTES)
            validation = audit_cell(cell, current, signature, trace)
            env.write_json(cell / 'strategy-validation.json', validation)
            if not validation['passed']:
                raise RuntimeError('Strategy pilot audit failed')
        except BaseException as exc:
            env.write_json(out / 'stopped.json', dict(cell=cell.name, error=repr(exc)))
            env.write_json(out / 'independent-summary.json', collect(out))
            raise
        print('DONE ' + cell.name, flush=True)
    env.write_json(out / 'independent-summary.json', collect(out))


if __name__ == '__main__':
    main()
