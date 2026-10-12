"""Describe request/transfer counters after complete strategy formal admission."""
import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_generation_strategy_formal import audit
from run_generation_strategy_pilot import read, CASES
from run_protection_ablation import metric
from analyze_streaming_tail import analyze as streaming


def analyze(directory):
    report = audit(directory)
    if not report['matrix_complete']:
        raise ValueError('Complete formal matrix required; no partial cost means')
    rows = []
    for row in report['rows']:
        cell = directory / f"{row['case']}-r{row['repeat']}"
        result, data = read(cell / 'result.json'), read(cell / 'requests.json')
        counters, values = result['vllm_delta'], {}
        for phase in ('queue', 'prefill', 'decode', 'inference'):
            key = 'vllm:request_' + phase + '_time_seconds'
            if counters[key + '_count'] != len(data['results']):
                raise ValueError('Duration counter request count differs: ' + cell.name)
            values[phase + '_mean_ms'] = 1000 * counters[key + '_sum'] / counters[key + '_count']
        for direction in ('h2d', 'd2h'):
            values[direction + '_staging_seconds'] = (
                metric(data['lmcache_after'], 'lmcache_mp_transfer_phase_elapsed_seconds_total', direction=direction, phase='staging')
                - metric(data['lmcache_before'], 'lmcache_mp_transfer_phase_elapsed_seconds_total', direction=direction, phase='staging'))
        timings = [t for p in result['protection_summary']['raw'] for t in p.get('timings', {}).values()]
        values['protection_callback_cpu_ms'] = sum(t['cpu_ns'] for t in timings) / 1e6
        values['protection_callback_wall_ms'] = sum(t['wall_ns'] for t in timings) / 1e6
        values['preemptions'] = counters.get('vllm:num_preemptions_total', 0)
        for key in ('ready_to_first_ms','ready_to_end_ms','session_completion_ms','client_queue_ms','reuse_ttft_ms'):
            values[key + '_p95'] = result[key]['p95']
        rows.append(dict(case=row['case'], repeat=row['repeat'], metrics=values, streaming=streaming(data)))
    aggregates = {}
    for name in CASES:
        points = [r['metrics'] for r in rows if r['case'] == name]
        aggregates[name] = {key:dict(mean=statistics.mean(values), stdev=statistics.stdev(values), individual=values)
            for key in points[0] for values in [[p[key] for p in points]]}
    return dict(rows=rows, aggregates=aggregates, scope='Descriptive counters only. Request phases, staging and callback wall time may overlap; '
        'not additive latency decomposition or DMA causal attribution. Callback totals cover instrumented protection helper methods only; '
        'zero means no recorded helper timing, not zero connector overhead. SSE fragments are not individual GPU tokens.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(cells=len(result['rows']))))
