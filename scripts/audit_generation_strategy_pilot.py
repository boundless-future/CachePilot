"""Offline pilot audit, including raw timestamps and all output differences."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import collect, CASES, read
from audit_replay_measurements import audit_cell


def audit(directory):
    report = collect(directory)
    outputs = {}
    for row in report['rows']:
        cell = directory / (row['case'] + '-r0')
        try:
            timing = audit_cell(cell)
            row['raw_timing'] = timing
            row['passed'] &= timing['passed']
            result = read(cell / 'result.json')
            requests = read(cell / 'requests.json')['results']
            outputs[row['case']] = {str(r['event_id']): r['output_sha256'] for r in requests}
            delta = result['vllm_delta']
            row['prefill_tokens'] = delta.get('vllm:request_prefill_kv_computed_tokens_sum')
            row['external_hit_tokens'] = delta.get('vllm:external_prefix_cache_hits_total')
            generations = read(cell / 'generation-summary.json')['generations']
            row['generation_count'] = len(generations)
            row['protection_events'] = result['protection_summary']['events']
            expected = len(requests) + 1  # One fixed warmup request.
            row['generation_counts_match'] = (
                len(generations) == expected and len({g['wire_id'] for g in generations}) == expected
                and all(row['protection_events'].get(k) == expected for k in
                    ('generation_end_submitted', 'generation_registered')))
            row['passed'] &= row['generation_counts_match']
        except (OSError, KeyError, ValueError, TypeError) as exc:
            row.update(passed=False, raw_audit_error=repr(exc))
    report['matrix_complete'] = all(r['passed'] for r in report['rows'])
    if not report['matrix_complete']:
        report['relative_selected_zero_pct'] = {}
    baseline = outputs.get('selected-zero', {})
    report['output_differences'] = {name: [event for event in baseline if values.get(event) != baseline[event]]
        for name, values in outputs.items()} if baseline else {}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(matrix_complete=result['matrix_complete'],
        rows=[{k: r.get(k) for k in ('case', 'passed', 'ttft_p95_ms', 'committed', 'fallbacks')} for r in result['rows']])))
    raise SystemExit(0 if result['matrix_complete'] else 1)
