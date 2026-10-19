"""Correlate existing diagnostic evidence; never align different runs' clocks."""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prefix_key(tokens):
    return hashlib.sha256(json.dumps(['', tokens], separators=(',', ':')).encode()).hexdigest()


def analyze(cell):
    trace, requests, warmup = (read(cell / name) for name in ('trace.json', 'requests.json', 'warmup.json'))
    events = trace['events']
    rows = [json.loads(line) for p in (cell / 'handoffs').glob('*.jsonl')
            for line in p.read_text(encoding='utf-8').splitlines() if line]
    service = [json.loads(line) for p in (cell / 'owned-events').glob('*.jsonl')
               for line in p.read_text(encoding='utf-8').splitlines() if line]

    def matches(row):
        return [e for e in events if len(e['prompt']) >= row['end']
                and prefix_key(e['prompt'][:row['end']]) == row['prefix_sha256']]

    original = [r for r in rows if r['event'] == 'generation_registered'
                and not r['request_id'].startswith(warmup['id'] + '-')]
    if not original:
        raise ValueError('No original request registration')
    # Even the earliest registration is AFTER replay start. No estimate of
    # the unknown replay origin, cross-run timestamp, or arrival-order ID map.
    first = min(r['monotonic_ns'] for r in original)
    protected = []
    for prepared in (r for r in rows if r['event'] == 'protection_prepared'):
        targets = matches(prepared)
        if not targets:
            raise ValueError('Unmapped protected prefix')
        tokens = targets[0]['prompt'][:prepared['end']]
        lower_ms = (prepared['monotonic_ns'] - first) / 1e6
        retrievals = []
        for r in rows:
            if r['event'] != 'scheduler_metadata' or r['direction'] != 'RETRIEVE':
                continue
            possible = matches(r)
            if not possible:
                raise ValueError('Unmapped RETRIEVE; cannot claim absence of use')
            same_prefix = any(e['prompt'][:prepared['end']] == tokens for e in possible)
            retrievals.append(dict(wire_id=r['wire_id'], start=r['start'], end=r['end'], skip=r['skip'],
                after_prepare_ms=(r['monotonic_ns'] - prepared['monotonic_ns']) / 1e6,
                sessions=sorted({e['session_id'] for e in possible}),
                possible_events=[e['event_id'] for e in possible],
                possibly_uses_protected_prefix=same_prefix and r['start'] + r['skip'] < prepared['end']))
        previous = []
        for r in rows:
            if (r['event'] != 'worker_bound' or r['source'] != 'default_manager'
                    or r['monotonic_ns'] >= prepared['monotonic_ns']
                    or r['start'] != 0 or r['end'] < prepared['end']):
                continue
            possible = matches(r)
            if not possible or possible[0]['prompt'][:prepared['end']] != tokens:
                continue
            submissions = [s for s in service if s['event'] == 'transfer_submitted' and s['kind'] == 'store'
                and s['request_id'] == r['wire_id'] and s['monotonic_ns'] > r['monotonic_ns']
                and {b for group in r['block_ids'] for b in group} <= {b for group in s['blocks'] for b in group}]
            successful = [s for s in service if s['event'] == 'transfer_retired' and s['kind'] == 'store'
                and s['request_id'] == r['wire_id'] and s['succeeded'] is True
                and s['monotonic_ns'] < prepared['monotonic_ns']
                and any(t['sequence'] == s['sequence'] and t['monotonic_ns'] < s['monotonic_ns'] for t in submissions)]
            if successful:
                previous.append(dict(wire_id=r['wire_id'], start=r['start'], end=r['end'],
                    retired_before_protection_ms=(prepared['monotonic_ns'] - successful[-1]['monotonic_ns']) / 1e6))
        slow = sorted(requests['results'], key=lambda r: r['ttft_ms'], reverse=True)[:5]
        protected.append(dict(wire_id=prepared['wire_id'], end=prepared['end'],
            source_sessions=sorted({e['session_id'] for e in targets}),
            source_possible_events=[e['event_id'] for e in targets],
            prepare_replay_ms_lower_bound=lower_ms,
            slowest_five=[dict(event_id=r['event_id'], ttft_ms=r['ttft_ms'], first_ms=r['chunk_arrivals_ms'][0],
                first_before_protection=r['chunk_arrivals_ms'][0] < lower_ms) for r in slow],
            earlier_successful_default_stores=previous,
            retrievals=retrievals, possible_protected_retrievals=sum(r['possibly_uses_protected_prefix'] for r in retrievals)))
    return dict(cell=cell.name, protection=protected,
        source_files={str(p.relative_to(cell)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(cell.rglob('*')) if p.is_file()},
        scope='Diagnostic evidence only; earlier successful STORE does not prove continued CPU residency; no formal timing claim')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('coverage', type=Path)
    parser.add_argument('--formal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    timings = []
    for cell in sorted(args.formal.glob('*-r*')):
        for p in (cell / 'protection-summary').glob('*.json'):
            data = read(p)
            if data['role'] == 'SCHEDULER' and data.get('timings'):
                timings.append(dict(cell=cell.name, committed=data['committed_batches'], timings=data['timings']))
    report = dict(coverage=[analyze(args.coverage / f'{mode}-r0') for mode in ('one', 'two')],
                  formal_overhead=timings, scope='Existing raw evidence only; no new GPU experiment')
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({r['cell']: [dict(prepare_ms_lower_bound=b['prepare_replay_ms_lower_bound'],
        possible_retrievals=b['possible_protected_retrievals'], earlier_stores=len(b['earlier_successful_default_stores']),
        slowest_five_finished=all(e['first_before_protection'] for e in b['slowest_five'])) for b in r['protection']]
        for r in report['coverage']}))
