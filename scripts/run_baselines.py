"""Sequential isolated GPU experiment cells; records failures and always stops services."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import time

import requests
from transformers import AutoTokenizer
from validate_environment import Service,ROOT,write_json
from generate_trace import generate
from replay_trace import replay
from analyze_replay import summarize


def read(url):
    r=requests.get(url,timeout=15);r.raise_for_status();return r.text


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--modes',nargs='+',default=['baseline','immediate','eviction','eviction-h5'])
    p.add_argument('--workloads',nargs='+',default=['fits-gpu','exceeds-gpu'])
    p.add_argument('--replay-mode',choices=['open','closed'],default='open')
    p.add_argument('--trace',type=Path,help='Use one externally prepared trace')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    api='http://127.0.0.1:8000'; cache_url='http://127.0.0.1:8081'
    os.environ.update(LMCACHE_PORT='5556',LMCACHE_HTTP_PORT='8081',VLLM_SERVER_DEV_MODE='1')
    for port in [8000,5556,8081]:
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',port))==0: raise RuntimeError(f'Port {port} occupied')
    model=str(ROOT/'models/Qwen3-4B'); tok=AutoTokenizer.from_pretrained(model)
    traces={name:generate(tok,4 if name=='fits-gpu' else 12,4,2048) for name in a.workloads}
    if a.trace:
        traces={'real-scaled':json.loads(a.trace.read_text())};a.workloads=['real-scaled']
    for name,t in traces.items(): write_json(a.output/(name+'-trace.json'),t)
    results=[]
    # Rotate policy order between repeats to reduce time-order bias.
    for rep in range(a.repeats):
        modes=a.modes[rep%len(a.modes):]+a.modes[:rep%len(a.modes)]
        for workload in a.workloads:
            for mode in modes:
                out=a.output/f'{workload}-{mode}-r{rep}';out.mkdir()
                if mode in {'decision','allocation-decision'}:
                    os.environ['CACHEPILOT_DECISION_DIR']=str(out/'decisions')
                write_json(out/'run-manifest.json',dict(mode=mode,replay_mode=a.replay_mode,
                    kv_cache_bytes=int(os.environ.get('KV_CACHE_BYTES',2147483648)),
                    decision_tracing=mode in {'decision','allocation-decision'},
                    source_sha256={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest()
                        for directory in ['scripts','configs'] for f in sorted((ROOT/directory).glob('*'))
                        if f.is_file() and f.suffix in {'.py','.sh','.json'}}))
                engine=Service(['bash',str(ROOT/'scripts/serve.sh'),mode],out/'vllm.log')
                cache=Service(['bash',str(ROOT/'scripts/lmcache-server.sh')],out/'lmcache.log')
                print(f'START {out.name}',flush=True)
                try:
                    if mode!='baseline': cache.start(cache_url+'/status')
                    engine.start(api+'/health')
                    # Same warmup, below one LMCache chunk; reset GPU cache after it.
                    r=requests.post(api+'/v1/completions',json=dict(model=model,prompt='Warm up inference.',max_tokens=16,temperature=0,ignore_eos=True),timeout=60)
                    r.raise_for_status()
                    reset=requests.post(api+'/reset_prefix_cache',timeout=30)
                    reset.raise_for_status();assert reset.json()['success']
                    before=read(api+'/metrics'); lbefore=read(cache_url+'/metrics') if mode!='baseline' else ''
                    data=asyncio.run(replay(traces[workload],api,model,a.replay_mode))
                    # Do not issue extra tokens to drain pending lazy work: that would change measured workload.
                    data.update(vllm_before=before,vllm_after=read(api+'/metrics'),lmcache_before=lbefore,
                        lmcache_after=read(cache_url+'/metrics') if mode!='baseline' else '',
                        policy=mode,workload=workload,repeat=rep,
                        trace_sha256=hashlib.sha256((a.output/(workload+'-trace.json')).read_bytes()).hexdigest())
                    write_json(out/'requests.json',data)
                    result=summarize(data);result.update(policy=mode,workload=workload,repeat=rep,replay_mode=a.replay_mode)
                    write_json(out/'summary.json',result);results.append(result)
                    if mode!='baseline': write_json(out/'status.json',json.loads(read(cache_url+'/status')))
                    print(f"DONE {out.name} errors={result['errors']} reuse_p95={result['reuse_ttft_ms']['p95']:.1f}ms",flush=True)
                    if result['errors']: raise RuntimeError('Replay errors; inspect requests.json')
                finally:
                    engine.stop();cache.stop()
                write_json(a.output/'summary.json',results)


if __name__=='__main__': main()
