"""Recompute formal gates and aggregates; no partial-matrix means."""
import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import CASES, TRACE_SHA, read, digest, audit_cell
from audit_replay_measurements import audit_cell as timing_audit


def audit_one(cell, name, repeat, signature, trace):
    row = audit_cell(cell, CASES[name], signature, trace)
    timing = timing_audit(cell)
    result = read(cell / 'result.json')
    manifest = read(cell / 'run-manifest.json')
    generations = read(cell / 'generation-summary.json')['generations']
    events = result['protection_summary']['events']
    requests = read(cell / 'requests.json')['results']
    expected = len(requests) + 1
    checks = dict(raw_timing=timing['passed'], repeat=result['repeat'] == manifest['repeat'] == repeat,
        generations=len(generations) == expected and len({g['wire_id'] for g in generations}) == expected
        and all(events.get(k) == expected for k in ('generation_end_submitted', 'generation_registered')))
    row['checks'].update(checks)
    row.update(passed=row['passed'] and all(checks.values()), timing_errors=timing['errors'],
        prefill_tokens=result['vllm_delta'].get('vllm:request_prefill_kv_computed_tokens_sum'),
        external_hit_tokens=result['vllm_delta'].get('vllm:external_prefix_cache_hits_total'),
        generation_count=len(generations),
        output_hashes={str(r['event_id']): r['output_sha256'] for r in requests})
    return row


def aggregate(rows):
    expected = {(name, repeat) for repeat in range(3) for name in CASES}
    if (len(rows) != 12 or {(r['case'], r['repeat']) for r in rows} != expected
            or not all(r['passed'] for r in rows)):
        return {}
    result = {}
    for name in CASES:
        points = sorted((r for r in rows if r['case'] == name), key=lambda r:r['repeat'])
        result[name] = {}
        for metric in ('ttft_p95_ms', 'h2d_bytes', 'd2h_bytes', 'prefill_tokens', 'committed'):
            values = [r[metric] for r in points]
            result[name][metric] = dict(mean=statistics.mean(values), stdev=statistics.stdev(values), individual=values)
    baseline = result['selected-zero']['ttft_p95_ms']['mean']
    for name in CASES:
        result[name]['relative_selected_zero_pct'] = 100 * (result[name]['ttft_p95_ms']['mean'] / baseline - 1)
    return result


def audit(directory):
    plan, trace = read(directory / 'plan.json'), read(directory / 'trace.json')
    from run_generation_strategy_formal import order
    if (plan['stage'] != 'formal' or plan['repeats'] != 3 or plan['cases'] != CASES
            or digest(trace) != TRACE_SHA or plan['orders'] != [order(i) for i in range(3)]):
        raise ValueError('Frozen formal design differs')
    rows, differences = [], []
    for repeat in range(3):
        for name in CASES:
            try:
                row = audit_one(directory / f'{name}-r{repeat}', name, repeat, plan['signature'], trace)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                row = dict(passed=False, error=repr(exc))
            rows.append(dict(case=name, repeat=repeat, **row))
        baseline = next(r for r in rows if r['repeat'] == repeat and r['case'] == 'selected-zero')
        for row in [r for r in rows if r['repeat'] == repeat]:
            if 'output_hashes' in baseline and 'output_hashes' in row:
                differences.append(dict(case=row['case'], repeat=repeat, eligible=row['passed'] and baseline['passed'],
                    event_ids=[k for k,v in baseline['output_hashes'].items() if row['output_hashes'].get(k) != v]))
    return dict(matrix_complete=all(r['passed'] for r in rows), passed_cells=sum(r['passed'] for r in rows),
        rows=rows, aggregates=aggregate(rows), output_differences=differences,
        scope='Three development repetitions; pilot excluded; SD is not confidence interval')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('matrix_complete', 'passed_cells', 'aggregates')}))
    raise SystemExit(0 if result['matrix_complete'] else 1)
