"""Offline candidate window and referenced-prefix diagnosis."""
import argparse
import collections
import json
from pathlib import Path


def analyze(cell):
    events=[json.loads(line) for p in sorted((cell/'handoff').glob('*.jsonl'))
            for line in p.read_text().splitlines() if line.strip()]
    raw=[e for e in events if e.get('stage')=='boundary']
    boundaries={e['diagnostic_step']:e for e in raw}
    if len(raw)!=len(boundaries) or sorted(boundaries)!=list(range(1,len(raw)+1)):
        raise ValueError('Missing or duplicate boundary sequence')
    bywire={}
    for e in events:
        if e.get('stage')!='pending_state':continue
        b=boundaries[e['diagnostic_step']]
        if (e['index']==0 and not e['live'] and e['finished'] and not e['native_inflight']
                and not e['protected_inflight'] and e['start']==0 and e['end']>=256
                and e['end']%256==0 and b['reserve'] is not None):
            bywire.setdefault(e['wire_id'],[]).append(dict(step=e['diagnostic_step'],free=b['free'],
                reserve=b['reserve'],margin=b['free']-b['reserve']-16,
                live=len(b['live']),budget_left=b['budget']-b['inflight_blocks']))
    windows=[]
    generations={g['wire_id']:g for g in json.loads((cell/'generation-summary.json').read_text())['generations']}
    for wire,points in bywire.items():
        terminals=[{k:e[k] for k in ('event','start','end','batch_id','source') if k in e}
            for e in events if e.get('wire_id')==wire and e['event'] in
            ('scheduler_built','scheduler_receipt','protection_metadata_committed','protection_receipt')]
        windows.append(dict(wire=wire,first_step=min(e['step'] for e in points),last_step=max(e['step'] for e in points),
            observations=len(points),min_margin=min(e['margin'] for e in points),max_margin=max(e['margin'] for e in points),
            points=points,terminal_events=terminals,generation_terminal=generations.get(wire)))
    points=[p for row in windows for p in row['points']]
    rejects=[e for e in events if e.get('stage')=='reference_ineligible']
    reasons=collections.Counter()
    for e in rejects:
        reason=tuple(k for k,test in [('referenced',any(b['refs'] for b in e['blocks'])),
            ('null',any(b['null'] for b in e['blocks'])),
            ('missing_hash',any(not b['hash_present'] for b in e['blocks']))] if test)
        reasons[str(reason)]+=1
    return dict(boundaries=len(raw),stage_counts=dict(collections.Counter(e['stage'] for e in events
        if e['event']=='protection_candidate_diagnostic')),eligible_wires=len(windows),
        eligible_observations=len(points),nonnegative_margin=sum(p['margin']>=0 for p in points),
        nonnegative_and_budget=sum(p['margin']>=0 and p['budget_left']>=16 for p in points),
        windows=windows,reference_rejections=len(rejects),reference_wires=len({e['wire_id'] for e in rejects}),
        rejection_reasons=dict(reasons),referenced_block_ids=dict(collections.Counter(str(b['id'])
            for e in rejects for b in e['blocks'] if b['refs'])),
        referenced_offsets=dict(collections.Counter(str(i) for e in rejects for i,b in enumerate(e['blocks']) if b['refs'])),
        refcounts=dict(collections.Counter(str(b['refs']) for e in rejects for b in e['blocks'] if b['refs'])),
        scope='Offline request/front-range prefilter, not full admission. Negative reserve margin is not actual '
            'allocation demand. Separate runs are not paired causal counterfactuals. Terminal events are wire-level; inspect ranges/batches.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result={name:analyze(args.directory/name) for name in ('development','validation')}
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({n:{k:v for k,v in r.items() if k!='windows'} for n,r in result.items()}))
