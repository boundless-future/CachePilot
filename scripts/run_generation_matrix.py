"""New-stack long-prefill references, independently gated pilot then formal."""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from run_fixed_baselines import configure, read, KV_BYTES, CASES, select_fixed, validate_completed, token_gate, strict_idle
from run_reference_baselines import cases_for, run_plain, resource_gate
from run_arrival_matrix import digest
from run_generation_pilot import TRACE_SHA


def extend_decode(base):
    from run_arrival_matrix import BASE_TRACE_SHA
    if digest(base) != BASE_TRACE_SHA:
        raise ValueError('Requires original held-out reference trace')
    trace = copy.deepcopy(base)
    trace['trace_id'] += '-decode192'
    trace['workload_variant'] = 'decode192'
    for event in trace['events']:
        if event['max_tokens'] != 48 or event['prompt_tokens'] + 192 > 8192:
            raise ValueError('Original budget or fixed context bound differs')
        event['max_tokens'] = 192
    return trace


def generation_config(original, case):
    result = configure(original, case)
    if case['mode'] != 'zero':
        raise ValueError('Only zero protection has passed the generation gate')
    result.update(kv_connector='GenerationLedgerConnector',
                  kv_connector_module_path='scripts.generation_ledger_connector')
    result['kv_connector_extra_config']['cachepilot.generation_ledger'] = True
    return result


def generation_closed(summary):
    return (bool(summary['generations']) and
        all(g['sealed'] and g['end_state'] == 'submitted' and g['error'] is None
            and all(o['state'] in ('terminal', 'dropped') for o in g['operations'])
            for g in summary['generations']) and
        all(summary[k] == 0 for k in ('pending_store', 'inflight_store', 'retrieves', 'pending_rpc'))
        and summary['deferred_ends'] == [])


def extra_checks(cell, case, result, trace):
    checks = dict(tokens=token_gate(cell, trace))
    if case['kind'] != 'native':
        checks['exact_server_empty'] = all(
            s.get('generation_observer') == dict(session_ids=[], owned_job_ids=[], transfer_wire_ids=[])
            for s in (result['status_before_engine_stop'], result['status_after_engine_stop']))
    if case['kind'] == 'lazy':
        checks['generation_closed'] = generation_closed(read(cell / 'generation-summary.json'))
    checks['no_hotpath_jsonl'] = all(
        json.loads(line)['event'] == 'owned_shutdown_drained'
        for path in cell.rglob('*.jsonl') for line in path.read_text().splitlines() if line.strip())
    return checks


def collect(directory):
    from summarize_reference_matrix import collect as base_collect
    report = base_collect(directory, lazy_configure=generation_config)
    plan = read(directory / 'plan.json')
    trace = read(directory / 'trace.json')
    for row in report['rows']:
        try:
            checks = extra_checks(directory / row['cell'], plan['cases'][row['case']],
                                  read(directory / row['cell'] / 'result.json'), trace)
            row.setdefault('checks', {}).update(checks)
            row['passed'] = row['passed'] and all(checks.values())
        except (OSError, KeyError, ValueError, TypeError) as exc:
            row.update(passed=False, generation_error=repr(exc))
    report['matrix_complete'] = all(r['passed'] for r in report['rows'])
    report['passed_cells'] = sum(r['passed'] for r in report['rows'])
    if not report['matrix_complete']:
        report['aggregates'] = {}
    for comparison in report['output_comparisons']:
        comparison['eligible'] = all(r['passed'] for r in report['rows']
            if r['repeat'] == comparison['repeat'] and r['case'] in ('native', comparison['case']))
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stage', required=True, choices=('pilot', 'formal'))
    p.add_argument('--diagnostic', type=Path, required=True)
    p.add_argument('--tuning', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--pilot', type=Path)
    p.add_argument('--workload', choices=('long-prefill', 'decode192'), default='long-prefill')
    p.add_argument('--long-prefill-formal', type=Path)
    p.add_argument('--base-reference', type=Path)
    p.add_argument('--stop-starting-at', required=True)
    args = p.parse_args()
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        p.error('Future timezone-aware cutoff required')
    if (args.stage == 'formal') != (args.pilot is not None):
        p.error('Only formal requires pilot')
    if (args.workload == 'decode192') != bool(args.long_prefill_formal and args.base_reference):
        p.error('decode192 requires both completed long-prefill formal and base reference')
    if args.workload == 'long-prefill' and (args.long_prefill_formal or args.base_reference):
        p.error('Long-prefill takes no decode prerequisites')
    import run_protection_ablation as ablation
    import validate_environment as environment
    from audit_generation_pilot import audit_cell
    from audit_generation_ownership import audit_cell as ownership_audit
    diagnostic = args.diagnostic / 'zero-r0'
    if not (read(args.diagnostic / 'validation.json')['passed'] and
            audit_cell(diagnostic)['passed'] and ownership_audit(diagnostic)['passed']):
        raise ValueError('Requires independently audited diagnostic including native ownership')
    tuning = read(args.tuning / 'plan.json')
    selection = select_fixed(validate_completed(args.tuning, tuning['signature'], 'formal', CASES, 3))
    if selection['selected'] != 'h5p0-d1':
        raise ValueError('Original frozen selection differs')
    trace = read(diagnostic / 'trace.json')
    if digest(trace) != TRACE_SHA:
        raise ValueError('Original long-prefill trace required')
    if args.workload == 'decode192':
        prior = read(args.long_prefill_formal / 'plan.json')
        if (prior['stage'] != 'formal' or prior['repeats'] != 3 or
                prior['signature']['trace_sha256'] != TRACE_SHA or
                not collect(args.long_prefill_formal)['matrix_complete']):
            raise ValueError('Complete long-prefill formal prerequisite required')
        from summarize_reference_matrix import collect as reference_collect
        base_plan = read(args.base_reference / 'plan.json')
        if (base_plan['stage'] != 'formal' or base_plan['repeats'] != 3 or
                not reference_collect(args.base_reference)['matrix_complete']):
            raise ValueError('Complete original base reference required')
        trace = extend_decode(read(args.base_reference / 'trace.json'))
    ablation.validate_trace(trace)
    cases = cases_for(selection['selected'], 'unprotected')
    sources = ablation.source_sha()
    diagnostic_sources = read(args.diagnostic / 'plan.json')['source_sha256']
    # The shutdown-only summary is explicitly new; lifecycle/manager/RPC and
    # original safety sources must still match the successful diagnostic.
    for path in ('scripts/generation_manager_bridge.py', 'scripts/generation_rpc_client.py',
                 'scripts/generation_session_ledger.py', 'scripts/generation_idle_pump.py',
                 'scripts/idle_finalize.py', 'scripts/owned_service.py',
                 'scripts/owned_service_protocol.py', 'scripts/protection_connector.py'):
        if sources[path] != diagnostic_sources[path]:
            raise ValueError('Diagnostic source differs: ' + path)
    signature = dict(source_sha256=sources, kv_cache_bytes=KV_BYTES,
        trace_sha256=digest(trace), cases=cases, gate='generation-' + args.workload + '-v1',
        scope=args.workload + '-new-generation-stack', hotpath_diagnostics=False)
    if args.pilot:
        prior = read(args.pilot / 'plan.json')
        if prior['stage'] != 'pilot' or prior['signature'] != signature or not collect(args.pilot)['matrix_complete']:
            raise ValueError('Full independently audited same-signature pilot required')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.stage == 'pilot' else 3
    environment.write_json(out / 'trace.json', trace)
    environment.write_json(out / 'plan.json', dict(stage=args.stage, signature=signature,
        cases=cases, repeats=repeats, selection=selection, diagnostic=str(args.diagnostic),
        tuning=str(args.tuning), pilot=str(args.pilot) if args.pilot else None,
        workload=args.workload, long_prefill_formal=str(args.long_prefill_formal),
        base_reference=str(args.base_reference),
        cutoff=args.stop_starting_at))
    current = None
    class MatrixService(environment.Service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '0'
            os.environ.pop('CACHEPILOT_HANDOFF_DIR', None)
            os.environ.pop('CACHEPILOT_OWNED_MP_DIR', None)
            if self.log.name == 'lmcache.log':
                self.command = [os.sys.executable,
                    str(ablation.ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555',
                    '--http-host', '127.0.0.1', '--http-port', '8080',
                    '--l1-size-gb', '16', '--l1-init-size-gb', '16', '--eviction-policy', 'LRU']
            elif self.log.name == 'vllm.log' and current['kind'] == 'lazy':
                i = self.command.index('--kv-transfer-config') + 1
                config = generation_config(json.loads(self.command[i]), current)
                self.command[i] = json.dumps(config)
                environment.write_json(self.log.parent / 'config.json', config)
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(self.log.parent / 'generation-summary.json')
            return super().start(health, timeout)
    ablation.Service = environment.Service = MatrixService
    old_drained = ablation.cache_drained
    ablation.cache_drained = lambda s: old_drained(s) and strict_idle(s)
    completed = []
    for repeat in range(repeats):
        names = list(cases)
        shift = repeat * 3 % len(names)
        for name in names[shift:] + names[:shift]:
            if datetime.now(timezone.utc) >= cutoff:
                environment.write_json(out / 'stopped-at-cutoff.json', dict(next_cell=f'{name}-r{repeat}', completed=len(completed)))
                return
            if ablation.source_sha() != sources:
                raise RuntimeError('Frozen sources changed')
            ablation.ports_free()
            current = cases[name]
            cell = out / f'{name}-r{repeat}'
            print('START ' + cell.name, flush=True)
            result = (ablation.run_cell(cell, current['mode'], repeat, trace,
                str(ablation.ROOT / 'models/Qwen3-4B'), KV_BYTES) if current['kind'] == 'lazy'
                else run_plain(cell, name, repeat, trace))
            checks = dict(result_passed=result['passed'], resources=resource_gate(current['kind'], result),
                          **extra_checks(cell, current, result, trace))
            validation = dict(passed=all(checks.values()), checks=checks, signature=signature, case=current)
            environment.write_json(cell / 'reference-validation.json', validation)
            if not validation['passed']:
                raise RuntimeError('Matrix cell failed: ' + cell.name)
            completed.append(dict(cell=cell.name, passed=True))
            environment.write_json(out / 'completed.json', completed)
            print('DONE ' + cell.name, flush=True)
    report = collect(out)
    environment.write_json(out / 'independent-summary.json', report)
    if not report['matrix_complete']:
        raise RuntimeError('Independent matrix audit failed')


if __name__ == '__main__':
    main()
