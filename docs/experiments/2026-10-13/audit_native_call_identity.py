"""Additional offline identity/time/gate-shape checks; leaves frozen audit intact."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
from audit_generation_pilot import rows


def check(calls, windows, allocations, generations):
    raw_ids={g['request_id'] for g in generations}
    by_step={r['step']:r for r in allocations}
    intervals={i+1:(w['monotonic_ns'],by_step[i+1]['monotonic_ns']) for i,w in enumerate(windows) if i+1 in by_step}
    nonnegative=('new_blocks','touched_free_blocks','reserved_blocks','watermark',
                 'num_new_tokens','num_new_computed_tokens','num_external_computed_tokens')
    checks=dict(calls_present=bool(calls),
        unique_steps=len(by_step)==len(allocations)==len(windows),
        actual_request_identity=all(r['request_id'] in raw_ids for r in calls),
        exact_step_time_interval=all(r['step'] in intervals and intervals[r['step']][0]<r['monotonic_ns']<intervals[r['step']][1] for r in calls),
        serial_time_order=all(a['serial']<b['serial'] and a['monotonic_ns']<b['monotonic_ns'] and a['step']<=b['step'] for a,b in zip(calls,calls[1:])),
        nonnegative_native_fields=all(all(type(r[k]) is int and r[k]>=0 for k in nonnegative) for r in calls),
        native_query_fields=all(all(type(d[k]) is int and d[k]>=0 for k in ('required','free')) for r in calls for d in r['demands']))
    shapes=[]
    for r in calls:
        ds=r['demands']
        if not r['full_sequence_must_fit']:
            shapes.append(len(ds)==1)
        elif len(ds)==1:
            shapes.append(not r['success'] and ds[0]['required']+r['watermark']>ds[0]['free'])
        elif len(ds)==2:
            shapes.append(ds[0]['required']+r['watermark']<=ds[0]['free'])
        else:
            shapes.append(False)
    checks['native_full_sequence_shape']=all(shapes)
    return dict(passed=all(checks.values()),checks=checks,calls=len(calls),
                observed_raw_ids=len({r['request_id'] for r in calls}),generation_raw_ids=len(raw_ids))


def main():
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();reports=[]
    for name in ('shared-one','shared-two'):
        cell=a.directory/name
        ev=rows(cell/'events','handoff-*.jsonl')
        snapshot=json.loads((cell/'generation-summary.json').read_text(encoding='utf-8'))
        report=check([r for r in ev if r['event']=='native_allocation_call'],
            [r for r in ev if r['event']=='shared_window'],
            [r for r in ev if r['event']=='structure_actual_allocation'],snapshot['generations'])
        reports.append(dict(case=name,**report))
    result=dict(passed=all(r['passed'] for r in reports),rows=reports,
                scope='Supplemental offline audit; does not change the original frozen-source report.')
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result));raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
