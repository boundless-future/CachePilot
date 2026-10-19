"""Offline whole-step matched matrix audit; never aggregate incomplete cells."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT / 'scripts'))
from run_whole_step_matrix import MODES, CASES, ADDITIONS, order, configuration
from run_fixed_baselines import read, KV_BYTES
from run_arrival_matrix import digest
from run_real_structure_pilot import TRACE_SHA
from run_generation_matrix import extra_checks
from run_reference_baselines import resource_gate
from audit_replay_measurements import audit_cell as timing_audit
from audit_real_structure import audit as provenance_audit
import run_protection_ablation as ablation


def audit_one(cell, mode, repeat, signature, trace, *, expected_config=None):
    result, manifest = read(cell / 'result.json'), read(cell / 'run-manifest.json')
    snapshot = read(cell / 'generation-summary.json')
    summary = ablation.merge_protection_summary(cell / 'protection-summary')
    logs = ablation.service_error_report(cell, read(cell / 'shutdown-context.json'))
    timing = timing_audit(cell)
    outputs = read(cell / 'requests.json')['results']
    generations = snapshot['generations']
    preemptions = result['vllm_delta'].get('vllm:num_preemptions_total')
    base = len(trace['events']) + 1
    expected = base + int(preemptions) if preemptions is not None else -1
    checks = dict(extra_checks(cell, CASES[mode], result, trace), result=result['passed'],
        resources=resource_gate('lazy', result), raw_timing=timing['passed'],
        config=read(cell / 'config.json') == signature['configs'][mode] == (
            configuration(mode) if expected_config is None else expected_config),
        source=manifest['source_sha256'] == signature['source_sha256'],
        trace=digest(trace) == digest(read(cell / 'trace.json')) == manifest['trace_sha256'] == signature['trace_sha256'] == TRACE_SHA,
        budget=manifest['kv_cache_bytes'] == signature['kv_cache_bytes'] == KV_BYTES,
        identity=manifest['mode'] == result['mode'] == mode and manifest['repeat'] == result['repeat'] == repeat,
        healthy=snapshot.get('protection_poisoned') is None and not snapshot.get('fault_error') and not snapshot.get('shutdown_error'),
        receipts=summary['committed_batches'] == summary['receipts'] and summary['failed_receipts'] == 0,
        coverage=(summary['committed_batches'] == 0 if mode == 'zero' else summary['committed_batches'] > 0),
        bounded=summary['peak_pinned_blocks'] <= MODES.index(mode) * 16,
        protection_closed=summary['shutdown_closed'], h2d=result['h2d_bytes'] > 0,
        fatal_logs=not logs['fatal_errors'],
        request_identities=len({g['request_id'] for g in generations}) == base and all(
            g['wire_id'].endswith(':' + g['request_id']) for g in generations),
        generations=len(generations) == len({g['wire_id'] for g in generations}) == expected and all(
            summary['events'].get(k) == expected for k in ('generation_registered', 'generation_end_submitted')))
    return dict(passed=all(checks.values()), checks=checks, requests=result['requests'], output_tokens=result['output_tokens'],
        ttft_p95_ms=result['ttft_ms']['p95'], h2d_bytes=result['h2d_bytes'], d2h_bytes=result['d2h_bytes'],
        committed=summary['committed_batches'], receipts=summary['receipts'],
        peak_pinned_blocks=summary['peak_pinned_blocks'], fallbacks=summary['fallbacks'],
        prefill_tokens=result['vllm_delta'].get('vllm:request_prefill_kv_computed_tokens_sum'),
        preemptions=preemptions, generation_count=len(generations),
        shutdown_teardown_errors=logs['shutdown_teardown_errors'],
        output_hashes={str(r['event_id']): r['output_sha256'] for r in outputs})


def audit(directory):
    directory = Path(directory)
    plan, trace = read(directory / 'plan.json'), read(directory / 'trace.json')
    repeats = 1 if plan['stage'] == 'pilot' else 3
    signature = plan['signature']
    if (plan['stage'] not in ('pilot', 'formal') or plan['repeats'] != repeats or plan['cases'] != CASES
            or plan['orders'] != [order(i) for i in range(repeats)]
            or signature['gate'] != 'whole-step-matched-v1' or signature['hotpath_diagnostics'] is not False
            or signature['trace_sha256'] != digest(trace) or digest(trace) != TRACE_SHA):
        raise ValueError('Invalid matched whole-step plan')
    with tarfile.open(directory / 'frozen-source.tar.gz') as archive:
        frozen = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                  for m in archive.getmembers() if m.isfile()}
    if frozen != signature['source_sha256']:
        raise ValueError('Frozen matrix source mismatch')
    prior_dir = directory / 'prerequisite'
    prior = read(prior_dir / 'plan.json')
    old = prior['signature']['source_sha256']
    if plan['stage'] == 'pilot':
        from audit_whole_step_mainline import audit as admission_audit
        changes = {p for p in set(old) | set(frozen) if old.get(p) != frozen.get(p)}
        if (prior['stage'] != 'kv' or set(prior['modes']) != {'one', 'two'}
                or changes != ADDITIONS or not admission_audit(prior_dir)['complete']):
            raise ValueError('KV prerequisite failed')
    elif prior['stage'] != 'pilot' or old != frozen or not audit(prior_dir)['matrix_complete']:
        raise ValueError('Same-source pilot prerequisite failed')
    provenance = provenance_audit(directory / 'input/built', directory / 'input/source', directory / 'input/catalogue.json')
    if provenance != read(directory / 'provenance-audit.json') or trace != read(directory / 'input/built/trace.json'):
        raise ValueError('Anonymous provenance differs')
    output, differences = [], []
    for repeat in range(repeats):
        for mode in MODES:
            try:
                row = audit_one(directory / f'{mode}-r{repeat}', mode, repeat, signature, trace)
            except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
                row = dict(passed=False, error=repr(exc))
            output.append(dict(mode=mode, repeat=repeat, **row))
        baseline = next(r for r in output if r['repeat'] == repeat and r['mode'] == 'zero')
        for row in (r for r in output if r['repeat'] == repeat):
            if 'output_hashes' in baseline and 'output_hashes' in row:
                differences.append(dict(mode=row['mode'], repeat=repeat,
                    event_ids=[k for k, v in baseline['output_hashes'].items() if row['output_hashes'].get(k) != v]))
    complete = all(r['passed'] for r in output)
    aggregates = {}
    if plan['stage'] == 'formal' and complete:
        for mode in MODES:
            points = [r for r in output if r['mode'] == mode]
            aggregates[mode] = {metric: dict(individual=[r[metric] for r in points],
                mean=statistics.mean(r[metric] for r in points), stdev=statistics.stdev(r[metric] for r in points))
                for metric in ('ttft_p95_ms', 'prefill_tokens', 'h2d_bytes', 'd2h_bytes', 'committed', 'preemptions')}
            baselines = [r for r in output if r['mode'] == 'zero']
            aggregates[mode]['paired_ttft_change_pct'] = [100 * (a['ttft_p95_ms'] / b['ttft_p95_ms'] - 1)
                                                       for a, b in zip(points, baselines)]
        baseline_mean = aggregates['zero']['ttft_p95_ms']['mean']
        for mode in MODES:
            aggregates[mode]['relative_zero_mean_pct'] = 100 * (aggregates[mode]['ttft_p95_ms']['mean'] / baseline_mean - 1)
    return dict(matrix_complete=complete, passed_cells=sum(r['passed'] for r in output), rows=output,
        aggregates=aggregates, output_differences=differences,
        scope='Original six-session trace; pilot excluded from formal; three descriptive repeats only')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.directory)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('matrix_complete', 'passed_cells', 'aggregates')}))
    raise SystemExit(0 if report['matrix_complete'] else 1)
