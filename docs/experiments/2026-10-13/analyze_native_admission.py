"""Summarize native call gates from an archived diagnostic without claims."""
import argparse,json
from collections import Counter,defaultdict
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 rows=[]
 for name in ('shared-one','shared-two'):
  events=[]
  for f in (a.directory/name/'events').glob('handoff-*.jsonl'):
   events += [json.loads(x) for x in f.read_text().splitlines()]
  calls=sorted((r for r in events if r['event']=='native_allocation_call'), key=lambda r:r['serial'])
  steps=defaultdict(list)
  for r in calls: steps[r['step']].append(r)
  reasons=Counter(); mixed=0
  for arr in steps.values():
   if any(r['success'] for r in arr) and any(not r['success'] for r in arr):mixed+=1
  for r in calls:
   if r['success']:continue
   reasons['full_sequence_gate' if r['full_sequence_must_fit'] and len(r['demands'])==1 else 'final_capacity_gate']+=1
   if r['num_external_computed_tokens']>0:reasons['external_load_rejected']+=1
   if r['num_new_tokens']==0:reasons['zero_compute_rejected']+=1
  rows.append(dict(case=name,calls=len(calls),successful=sum(r['success'] for r in calls),
   rejected=sum(not r['success'] for r in calls),gross_new_blocks=sum(r['new_blocks'] for r in calls),
   free_hit_touch_blocks=sum(r['touched_free_blocks'] for r in calls),
   zero_compute_calls_with_new_blocks=sum(r['num_new_tokens']==0 and r['new_blocks']>0 for r in calls),
   external_load_calls=sum(r['num_external_computed_tokens']>0 for r in calls),
   mixed_success_rejection_steps=mixed,max_single_call_new_blocks=max(r['new_blocks'] for r in calls),
   max_single_call_touch=max(r['touched_free_blocks'] for r in calls),
   max_native_required=max(d['required'] for r in calls for d in r['demands']),
   rejection_reasons=dict(reasons),scope='Descriptive per-call diagnostic; not a strategy or whole-step safe-bound proof.'))
 a.output.write_text(json.dumps(dict(passed=True,rows=rows),indent=2)+'\n',encoding='utf-8');print(json.dumps(rows))
if __name__=='__main__':main()
