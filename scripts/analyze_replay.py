"""Aggregate raw latency observations and Prometheus counter deltas."""
import argparse
import json
from pathlib import Path
import statistics


def percentile(values,p):
    v=sorted(values)
    if not v: return None
    x=(len(v)-1)*p/100; i=int(x)
    return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)


def counters(text):
    result={}
    for line in text.splitlines():
        if not line or line.startswith('#'): continue
        name=line.split('{')[0].split(' ')[0]
        try: value=float(line.rsplit(' ',1)[1])
        except ValueError: continue
        result[name]=result.get(name,0)+value
    return result


def summarize(data):
    rows=[r for r in data['results'] if r['error'] is None]
    result=dict(requests=len(data['results']),errors=len(data['results'])-len(rows))
    for key in ['ttft_ms','ready_to_first_ms','e2e_ms','ready_to_end_ms','client_queue_ms']:
        values=[r[key] for r in rows if r[key] is not None]
        result[key]={f'p{p}':percentile(values,p) for p in [50,95,99]}
        result[key]['mean']=statistics.mean(values) if values else None
    for phase,selected in [('cold',[r for r in rows if r['turn']==0]),('reuse',[r for r in rows if r['turn']>0])]:
        result[phase+'_ttft_ms']={f'p{p}':percentile([r['ttft_ms'] for r in selected],p) for p in [50,95]}
    sessions={}
    for r in rows: sessions.setdefault(r['session_id'],[]).append(r)
    spans=[max(r['finished_ms'] for r in rs)-min(r['scheduled_ms'] for r in rs) for rs in sessions.values()]
    result['session_completion_ms']={'mean':statistics.mean(spans) if spans else None,'p95':percentile(spans,95)}
    result['elapsed_ms']=max((r['finished_ms'] for r in rows),default=0)
    result['output_tokens']=sum(r['usage']['completion_tokens'] for r in rows)
    for source in ['vllm','lmcache']:
        before=counters(data.get(source+'_before','')); after=counters(data.get(source+'_after',''))
        result[source+'_delta']={k:v-before.get(k,0) for k,v in after.items() if k.endswith(('_total','_sum','_count')) and not k.startswith(('python','process','http_'))}
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('inputs',nargs='+',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();a.output.write_text(json.dumps({str(f):summarize(json.loads(f.read_text())) for f in a.inputs},indent=2)+'\n')


if __name__=='__main__': main()
