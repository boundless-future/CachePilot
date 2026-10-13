"""Offline prefix demand after completion, not measured protection efficacy."""
import argparse
import hashlib
import json
from pathlib import Path


def analyze(trace, requests):
    events = trace['events']
    rows = requests['results']
    if len(events) != len(rows) or {e['event_id'] for e in events} != {r['event_id'] for r in rows}:
        raise ValueError('Exact request identities required')
    by_id = {r['event_id']: r for r in rows}
    if len(by_id) != len(rows) or any(r['error'] is not None for r in rows):
        raise ValueError('Complete distinct requests required')
    output = []
    for event in events:
        ids = event['prompt']
        if not isinstance(ids, list):
            raise ValueError('Frozen tokenizer IDs required')
        row = by_id[event['event_id']]
        candidates = []
        for chunks in (1, 2):
            n = chunks * 256
            if len(ids) < n:
                continue
            matches = [other for other in events if other['event_id'] != event['event_id']
                       and len(other['prompt']) >= n and other['prompt'][:n] == ids[:n]]
            future = sorted((dict(event_id=o['event_id'], session_id=o['session_id'],
                sent_gap_ms=by_id[o['event_id']]['sent_ms'] - row['finished_ms'])
                for o in matches if by_id[o['event_id']]['sent_ms'] > row['finished_ms']),
                key=lambda o: (o['sent_gap_ms'], o['event_id']))
            candidates.append(dict(chunks=chunks, prefix_sha256=hashlib.sha256(
                json.dumps(ids[:n], separators=(',', ':')).encode()).hexdigest(),
                later_after_completion=future, all_other_matching_requests=len(matches)))
        output.append(dict(event_id=event['event_id'], session_id=event['session_id'], prefixes=candidates))
    summaries = {}
    for chunks in (1, 2):
        candidates = [p for row in output for p in row['prefixes'] if p['chunks'] == chunks]
        eligible = [p for p in candidates if p['later_after_completion']]
        summaries[str(chunks)] = dict(completed_requests_with_full_prefix=len(candidates),
            requests_with_later_matching_demand=len(eligible),
            unique_prefixes_with_later_demand=len({p['prefix_sha256'] for p in eligible}),
            first_later_gaps_ms=[p['later_after_completion'][0]['sent_gap_ms'] for p in eligible])
    return dict(rows=output, summary=summaries,
        scope='Post-hoc input prefix equality after observed client completion. Not protection candidate identity, '
              'actual GPU/CPU cache miss, protected STORE availability, H2D provenance, or a causal benefit estimate. '
              'Repeated overlapping requests can count the same prefix more than once. Arrival replay has no output feedback.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('cell', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = analyze(json.loads((args.cell/'trace.json').read_text(encoding='utf-8')),
                     json.loads((args.cell/'requests.json').read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result['summary']))
