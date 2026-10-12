"""Post-hoc phase/stream description after the full generation matrix audit."""
import argparse
import json
from pathlib import Path
import statistics
from run_generation_matrix import collect
from summarize_workload_phases import phase_metrics, phase_for
from analyze_streaming_tail import analyze


def describe(directory):
    matrix = collect(directory)
    if not matrix['matrix_complete'] or matrix['stage'] != 'formal':
        raise ValueError('Requires complete independently audited formal matrix')
    rows = []
    for cell in matrix['rows']:
        data = json.loads((directory / cell['cell'] / 'requests.json').read_text(encoding='utf-8'))
        rows.append(dict(cell=cell['cell'], case=cell['case'], repeat=cell['repeat'],
            phases=phase_metrics(data['results']), streaming=analyze(data),
            phase_streaming={p: analyze(dict(results=[r for r in data['results'] if phase_for(r) == p]))
                for p in ('early', 'pressure', 'revisit')}))
    aggregates = {}
    for name in matrix['aggregates']:
        group = [r for r in rows if r['case'] == name]
        aggregates[name] = {}
        for phase in ('early', 'pressure', 'revisit'):
            aggregates[name][phase] = {}
            for metric in group[0]['phases'][phase]:
                values = [r['phases'][phase][metric] for r in group]
                aggregates[name][phase][metric] = dict(mean=statistics.mean(values),
                    stdev=statistics.stdev(values), individual=values)
    return dict(matrix_complete=True, trace_sha256=matrix['signature']['trace_sha256'],
        workload=matrix['signature']['scope'], rows=rows, aggregates=aggregates,
        scope='Post-hoc 48 early / 8 pressure / 12 revisit split; not a new tuning criterion, '
              'server phase profile or DMA causality proof. Streaming durations are SSE observations.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = describe(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(complete_cells=len(result['rows']), workload=result['workload'])))


if __name__ == '__main__':
    main()
