"""Retrospective warning opportunities, not an online predictor or DMA simulation."""
import argparse
import json
from pathlib import Path
from analyze_decision_timeline import analyze


def opportunities(rows):
    requests = {r['request_id']: r for r in rows if r['event'] == 'request'}
    cases = []
    for case in analyze(rows)['cases']:
        if not case['allocation_evidence']:
            continue
        first = case['allocation_evidence'][0]
        context = first['context'] or {}
        incoming = requests.get(context.get('request_id'))
        if incoming is None:
            continue
        arrival, loss = incoming['monotonic_ns'], first['monotonic_ns']
        snapshots = []
        for row in rows:
            if row['event'] != 'drain' or not arrival <= row['monotonic_ns'] < loss:
                continue
            for pending in row['pending']:
                if ((pending['request_id'], pending['start'], pending['end']) ==
                        (case['request_id'], case['start'], case['end']) and not pending['blocked']):
                    snapshots.append(dict(step=row['step'],
                        lead_ms=(loss-row['monotonic_ns'])/1e6,rank=pending['nearest_free_rank']))
                    break
        cases.append(dict(request_id=case['request_id'],start=case['start'],end=case['end'],
            loss_step=first['upcoming_step'],allocating_request=incoming['request_id'],
            allocation_request_known_ms=(loss-arrival)/1e6,
            observed_unblocked_pending_drains=len(snapshots),
            earliest=snapshots[0] if snapshots else None,
            latest=snapshots[-1] if snapshots else None))
    return dict(note='Retrospective observations only: not a predictor, not evidence DMA would finish, not independent request counts.',
        allocator_confirmed_operations_with_request_event=len(cases),
        operations_with_prior_pending_drain=sum(bool(c['earliest']) for c in cases),cases=cases)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('ledger',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    rows=[json.loads(line) for line in args.ledger.read_text().splitlines()]
    result=opportunities(rows)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))


if __name__=='__main__':main()
