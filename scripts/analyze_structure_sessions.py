"""Describe all six source sessions after complete structural formal admission."""
import argparse
import json
from pathlib import Path
import statistics

from analyze_replay import percentile
from run_real_structure_pilot import collect_structural


def session_rows(trace, data):
    events = {e['event_id']: e for e in trace['events']}
    rows = data['results']
    if len(rows) != len(events) or {r['event_id'] for r in rows} != set(events):
        raise ValueError('Every trace request must appear exactly once')
    grouped = {}
    for row in rows:
        event = events[row['event_id']]
        if (row['error'] is not None or row['session_id'] != event['session_id']
                or row['turn'] != event['turn']):
            raise ValueError('Request/session identity or completion differs')
        grouped.setdefault(event['source_file'], []).append(row)
    result = {}
    for name, group in sorted(grouped.items()):
        result[name] = dict(requests=len(group),
            completion_ms=max(r['finished_ms'] for r in group) - min(r['scheduled_ms'] for r in group),
            **{key + '_p95': percentile([r[key] for r in group], 95)
               for key in ('ttft_ms', 'ready_to_first_ms', 'ready_to_end_ms', 'client_queue_ms')})
    return result


def analyze(directory):
    matrix = collect_structural(directory)
    if (not matrix['matrix_complete'] or matrix['stage'] != 'formal'
            or matrix['expected_cells'] != 12
            or matrix['signature']['observer_interval_seconds'] != 0):
        raise ValueError('Complete unobserved twelve-cell structural formal required')
    trace = json.loads((directory / 'trace.json').read_text(encoding='utf-8'))
    rows = []
    for cell in matrix['rows']:
        data = json.loads((directory / cell['cell'] / 'requests.json').read_text(encoding='utf-8'))
        rows.append(dict(cell=cell['cell'], case=cell['case'], repeat=cell['repeat'],
                         sessions=session_rows(trace, data)))
    aggregates = {}
    for case in matrix['aggregates']:
        group = [r for r in rows if r['case'] == case]
        aggregates[case] = {name: {key: dict(mean=statistics.mean(values),
            stdev=statistics.stdev(values), individual=values)
            for key in values_by_key for values in [[r['sessions'][name][key] for r in group]]}
            for name, values_by_key in group[0]['sessions'].items()}
    return dict(rows=rows, aggregates=aggregates, trace_sha256=matrix['signature']['trace_sha256'],
        scope='All six source sessions, post-hoc descriptive analysis; no selected subset or tuning. '
              'Session completion includes artificial offered-arrival spans; not live Agent task latency.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = analyze(args.directory)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
