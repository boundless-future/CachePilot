"""Descriptive cost counters of complete matrices, without additive attribution."""
import argparse
import json
from pathlib import Path
import statistics
from run_generation_matrix import collect
from run_protection_ablation import metric


def analyze(directory):
    matrix = collect(directory)
    if not matrix['matrix_complete'] or matrix['stage'] != 'formal':
        raise ValueError('Complete formal matrix required')
    rows = []
    for cell in matrix['rows']:
        result = json.loads((directory / cell['cell'] / 'result.json').read_text(encoding='utf-8'))
        data = json.loads((directory / cell['cell'] / 'requests.json').read_text(encoding='utf-8'))
        counters = result['vllm_delta']
        observations = {}
        for phase in ('queue', 'prefill', 'decode', 'inference'):
            prefix = 'vllm:request_' + phase + '_time_seconds'
            count = counters[prefix + '_count']
            if count != cell['requests']:
                raise ValueError('Request duration count mismatch: ' + cell['cell'])
            observations[phase + '_mean_ms'] = 1000 * counters[prefix + '_sum'] / count
        observations['preemptions'] = counters.get('vllm:num_preemptions_total', 0)
        if cell['case'] != 'native':
            for direction in ('h2d', 'd2h'):
                observations[direction + '_staging_elapsed_seconds'] = (
                    metric(data['lmcache_after'], 'lmcache_mp_transfer_phase_elapsed_seconds_total',
                           direction=direction, phase='staging') -
                    metric(data['lmcache_before'], 'lmcache_mp_transfer_phase_elapsed_seconds_total',
                           direction=direction, phase='staging'))
        rows.append(dict(cell=cell['cell'], case=cell['case'], repeat=cell['repeat'], metrics=observations))
    aggregates = {}
    for case in matrix['aggregates']:
        group = [r['metrics'] for r in rows if r['case'] == case]
        aggregates[case] = {key: dict(mean=statistics.mean(values), stdev=statistics.stdev(values), individual=values)
            for key in group[0] for values in [[r[key] for r in group]]}
    return dict(matrix_complete=True, rows=rows, aggregates=aggregates,
        scope='Post-hoc request duration counters, not exclusive kernel timings. '
              'Transfer staging durations may overlap requests and other phases; '
              'do not add these metrics into a latency budget or infer DMA causality.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = analyze(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(complete_cells=len(result['rows']))))


if __name__ == '__main__':
    main()
