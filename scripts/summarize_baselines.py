"""Produce per-run and repeated-run summaries from saved requests, without rerunning GPU."""
import argparse
import collections
import json
from pathlib import Path
import re
import statistics
from analyze_replay import summarize


def metric(text,name,**labels):
    total=0
    for line in text.splitlines():
        if not (line.startswith(name+'{') or line.startswith(name+' ')):continue
        if all(f'{k}="{v}"' in line for k,v in labels.items()): total+=float(line.rsplit(' ',1)[1])
    return total


def main():
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();runs=[];groups=collections.defaultdict(list)
    for file in sorted(a.directory.glob('*/requests.json')):
        data=json.loads(file.read_text()); r=summarize(data)
        r.update(policy=data['policy'],workload=data['workload'],repeat=data['repeat'],trace_sha256=data['trace_sha256'])
        v=r['vllm_delta'];n=r['requests']
        r.update(queue_mean_ms=1000*v.get('vllm:request_queue_time_seconds_sum',0)/n,
            prefill_mean_ms=1000*v.get('vllm:request_prefill_time_seconds_sum',0)/n,
            prefill_computed_tokens=v.get('vllm:request_prefill_kv_computed_tokens_sum'),
            gpu_hit_tokens=v.get('vllm:prefix_cache_hits_total',0),
            external_hit_tokens=v.get('vllm:external_prefix_cache_hits_total',0),
            output_tokens_per_second=r['output_tokens']/(r['elapsed_ms']/1000))
        for direction in ['d2h','h2d']:
            # Staging alone measures the DMA path; summing kernel+staging double counts bytes.
            name='lmcache_mp_transfer_phase_bytes_total'
            r[direction+'_bytes']=metric(data['lmcache_after'],name,direction=direction,phase='staging')-metric(data['lmcache_before'],name,direction=direction,phase='staging')
        log=(file.parent/'vllm.log').read_text()
        r['policy_ledger']=re.findall(r'Lazy offload final counters: ([^\x1b\n]+)',log)
        r['shutdown_error_lines']=[l for l in log.splitlines() if 'ERROR' in l]
        r['output_hashes']={str(x['event_id']):x['output_sha256'] for x in data['results']}
        runs.append(r);groups[(r['workload'],r['policy'])].append(r)
    aggregates=[]
    for (workload,policy),rs in groups.items():
        values={}
        for key in ['reuse_ttft_ms','ttft_ms','e2e_ms']:
            xs=[r[key]['p95'] for r in rs]
            values[key+'_p95']={'mean':statistics.mean(xs),'min':min(xs),'max':max(xs),'individual':xs}
        for key in ['queue_mean_ms','prefill_mean_ms','prefill_computed_tokens','gpu_hit_tokens','external_hit_tokens','d2h_bytes','h2d_bytes','output_tokens_per_second']:
            xs=[r[key] for r in rs if r[key] is not None]
            values[key]={'mean':statistics.mean(xs),'min':min(xs),'max':max(xs)} if xs else None
        aggregates.append(dict(workload=workload,policy=policy,repeats=len(rs),errors=sum(r['errors'] for r in rs),metrics=values))
    # Compare output equality as a diagnostic, not a substitute for quality regression.
    comparisons=[]
    for r in runs:
        ref=next((x for x in runs if x['policy']=='baseline' and x['repeat']==r['repeat'] and x['workload']==r['workload']),None)
        if ref:
            comparisons.append(dict(workload=r['workload'],policy=r['policy'],repeat=r['repeat'],
                mismatches=sum(v!=ref['output_hashes'].get(k) for k,v in r['output_hashes'].items()),requests=len(r['output_hashes'])))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(dict(runs=runs,aggregates=aggregates,output_comparisons=comparisons),indent=2)+'\n')
    print(json.dumps(aggregates,indent=2))


if __name__=='__main__':main()
