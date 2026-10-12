"""Audit unchanged historical held-out inputs and matched strategy cells."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import CASES, TRACE_SHA, read, digest
from audit_generation_strategy_formal import audit_one, aggregate
from run_generation_strategy_formal import order

VALIDATION_SHA = '015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b'
STRUCTURE = ('event_id', 'session_id', 'turn', 'arrival_ms', 'think_ms', 'max_tokens', 'temperature', 'seed')


def input_audit(development, validation):
    from generate_trace import validate_trace
    validate_trace(development)
    validate_trace(validation)
    old, new = development['events'], validation['events']
    checks = dict(development_digest=digest(development) == TRACE_SHA,
        validation_digest=digest(validation) == VALIDATION_SHA,
        requests=len(old) == len(new) == 68,
        structure=[{k:e[k] for k in STRUCTURE} for e in old] == [{k:e[k] for k in STRUCTURE} for e in new],
        disjoint=not ({e['prompt_sha256'] for e in old} & {e['prompt_sha256'] for e in new}),
        unique=len({e['prompt_sha256'] for e in new}) == len(new))
    return dict(passed=all(checks.values()), checks=checks,
        development_prompt_tokens=sum(e['prompt_tokens'] for e in old),
        validation_prompt_tokens=sum(e['prompt_tokens'] for e in new),
        lengths_changed=sum(a['prompt_tokens'] != b['prompt_tokens'] for a,b in zip(old,new)),
        scope='Previously frozen synthetic namespace; not unseen real Agent data or equal token lengths')


def audit(directory):
    plan, trace = read(directory / 'plan.json'), read(directory / 'trace.json')
    repeats = 1 if plan['stage'] == 'pilot' else 3
    if (plan['stage'] not in ('pilot', 'formal') or plan['repeats'] != repeats or plan['cases'] != CASES
            or plan['orders'] != [order(i) for i in range(repeats)]):
        raise ValueError('Frozen validation design differs')
    inputs = input_audit(read(directory / 'development-trace.json'), trace)
    if not inputs['passed'] or inputs != plan['input_audit']:
        raise ValueError('Frozen validation input audit differs')
    rows, differences = [], []
    for repeat in range(repeats):
        for name in CASES:
            try:
                row = audit_one(directory / f'{name}-r{repeat}', name, repeat, plan['signature'], trace)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                row = dict(passed=False, error=repr(exc))
            rows.append(dict(case=name, repeat=repeat, **row))
        baseline = next(r for r in rows if r['case'] == 'selected-zero' and r['repeat'] == repeat)
        for row in [r for r in rows if r['repeat'] == repeat]:
            if 'output_hashes' in row and 'output_hashes' in baseline:
                differences.append(dict(case=row['case'], repeat=repeat, eligible=row['passed'] and baseline['passed'],
                    event_ids=[k for k,v in baseline['output_hashes'].items() if row['output_hashes'].get(k) != v]))
    complete = all(r['passed'] for r in rows)
    return dict(matrix_complete=complete, passed_cells=sum(r['passed'] for r in rows), rows=rows,
        aggregates=aggregate(rows) if plan['stage'] == 'formal' else {}, output_differences=differences,
        input_audit=inputs, scope='Historical synthetic input validation; pilot excluded from formal means')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('matrix_complete', 'passed_cells', 'aggregates')}))
    raise SystemExit(0 if result['matrix_complete'] else 1)
