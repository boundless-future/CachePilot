"""Link candidate loss to prior decisions, allocator reuse and later lookup.

This reports observed associations, not a counterfactual latency saving.
One scheduler JSONL per invocation prevents mixing runs/request IDs/step clocks.
"""
import argparse
import collections
import json
from pathlib import Path


def op_key(row):
    return row['request_id'], row['start'], row['end']


def analyze(rows):
    requests = {r['request_id']: r for r in rows if r['event']=='request'}
    drains = {r['step']: r for r in rows if r['event']=='drain'}
    schedules = {r['step']: r for r in rows if r['event']=='scheduled'}
    lookups = [r for r in rows if r['event']=='lookup']
    previous = {}
    allocations = collections.defaultdict(list)
    cases = []
    for row in rows:
        event = row['event']
        if event=='admission' and row['accepted']:
            previous.pop(op_key(row),None)
            allocations.pop(op_key(row),None)
        elif event=='allocation':
            for affected in row['affected']:
                allocations[op_key(affected)].append(dict(
                    upcoming_step=row['upcoming_step'], monotonic_ns=row['monotonic_ns'],
                    allocated_blocks=row['allocated_blocks'],context=row['context'],
                    recycled_blocks=len(affected['recycled_block_ids']),
                    intact_before_count=len(affected['intact_before_ids'])))
        elif event=='drain':
            for pending in row['pending']:
                previous[op_key(pending)] = dict(step=row['step'],danger_depth=row['danger_depth'],
                    rank=pending['nearest_free_rank'],blocked=pending['blocked'],
                    new_blocks=row['new_blocks'],free_blocks=row['free_blocks'])
        elif event=='dropped_evicted':
            key=op_key(row);prev=previous.get(key)
            related=allocations.get(key,[])
            # Only count proven recycling of still-intact candidate hashes.
            recycled=[a for a in related if a['intact_before_count']>0]
            loss_time=recycled[0]['monotonic_ns'] if recycled else row['monotonic_ns']
            source=requests.get(row['request_id'])
            matches=[]
            seen=set()
            for lookup in lookups:
                rid=lookup['request_id'];req=requests.get(rid)
                if rid in seen or rid==row['request_id'] or not req:continue
                if lookup['monotonic_ns']<loss_time:continue
                if source and req['prompt_tokens']<=source['prompt_tokens']:continue
                if req['prefixes'].get(str(row['end']))!=row['prefix_sha256']:continue
                seen.add(rid)
                hit=lookup['gpu_computed_tokens']+lookup['external_tokens']
                matches.append(dict(request_id=rid,step=lookup['step'],cached_prefix_tokens=hit,
                    missing_tokens_in_range=max(0,row['end']-max(row['start'],hit))))
            current=drains.get(row['step'],{})
            sched=schedules.get(row['step'],{})
            cases.append(dict(request_id=row['request_id'],step=row['step'],start=row['start'],end=row['end'],
                prefix_sha256=row['prefix_sha256'],changed_blocks=len(row['changed_block_ids']),
                first_lost=row.get('first_lost'),drop_kind=row.get('drop_kind','unknown'),
                prior=prev,allocation_evidence=recycled,
                reported_new_blocks=current.get('new_blocks'),
                actual_allocated_blocks=sched.get('actual_allocated_blocks'),
                age_seconds=row['age_seconds'],later_lookups=matches))
    return dict(drop_operations=len(cases),
        first_lost_operations=(sum(c['first_lost'] is True for c in cases)
            if all(c['first_lost'] is not None for c in cases) else None),
        hash_changed_operations=sum(c['changed_blocks']>0 for c in cases),
        suffix_without_hash_change=sum(c['drop_kind']=='prefix_suffix' for c in cases),
        allocator_confirmed_operations=sum(bool(c['allocation_evidence']) for c in cases),
        drops_with_later_miss=sum(any(m['missing_tokens_in_range']>0 for m in c['later_lookups']) for c in cases),
        cases=cases)


def main():
    p=argparse.ArgumentParser();p.add_argument('ledger',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=analyze([json.loads(line) for line in args.ledger.read_text().splitlines()])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))


if __name__=='__main__':main()
