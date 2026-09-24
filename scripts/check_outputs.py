"""Serial cold-prefill vs CPU-prefix retrieval regression for actual benchmark prompts."""
import argparse
import json
import os
import socket
import time
from pathlib import Path
import requests
from validate_environment import Service,ROOT,write_json
from analyze_replay import counters
from generate_trace import validate_trace


def main():
    p=argparse.ArgumentParser();p.add_argument('--trace',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    for port in [8000,5556,8081]:
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',port))==0: raise RuntimeError(f'Port {port} occupied')
    os.environ.update(LMCACHE_PORT='5556',LMCACHE_HTTP_PORT='8081',VLLM_SERVER_DEV_MODE='1')
    api='http://127.0.0.1:8000';cache_url='http://127.0.0.1:8081';model=str(ROOT/'models/Qwen3-4B')
    trace=json.loads(a.trace.read_text());validate_trace(trace);all_results={}
    for mode in ['baseline','immediate']:
        out=a.output/mode;out.mkdir()
        engine=Service(['bash',str(ROOT/'scripts/serve.sh'),mode,'--max-num-seqs','1'],out/'vllm.log')
        cache=Service(['bash',str(ROOT/'scripts/lmcache-server.sh')],out/'lmcache.log');rows=[]
        try:
            if mode=='immediate':cache.start(cache_url+'/status')
            engine.start(api+'/health')
            for e in trace['events']:
                # Tiny pump lets pending store completions release their block references.
                for attempt in range(20):
                    # Allow asynchronous completions to be received on later engine steps.
                    requests.post(api+'/v1/completions',json=dict(model=model,prompt='Tick.',max_tokens=8,temperature=0,ignore_eos=True),timeout=60).raise_for_status()
                    time.sleep(.1)
                    reset=requests.post(api+'/reset_prefix_cache',timeout=20);reset.raise_for_status()
                    if reset.json()['success']: break
                else: raise RuntimeError('GPU reset failed after 20 bounded drain attempts')
                before=counters(requests.get(api+'/metrics',timeout=10).text)
                payload=dict(model=model,prompt=e['prompt'],max_tokens=48,temperature=0,seed=42,ignore_eos=True,logprobs=5)
                r=requests.post(api+'/v1/completions',json=payload,timeout=90);r.raise_for_status()
                data=r.json()
                assert data['usage']['prompt_tokens']==e['prompt_tokens'], 'input token mismatch'
                assert data['usage']['completion_tokens']==48, 'output token mismatch'
                after=counters(requests.get(api+'/metrics',timeout=10).text)
                rows.append(dict(event_id=e['event_id'],turn=e['turn'],response=data,reset_attempts=attempt+1,
                    gpu_hit_tokens=after.get('vllm:prefix_cache_hits_total',0)-before.get('vllm:prefix_cache_hits_total',0),
                    external_hit_tokens=after.get('vllm:external_prefix_cache_hits_total',0)-before.get('vllm:external_prefix_cache_hits_total',0)))
                write_json(out/'responses.json',rows)
        finally:engine.stop();cache.stop()
        all_results[mode]=rows
    diffs=[]
    for arow,brow in zip(all_results['baseline'],all_results['immediate']):
        ac=arow['response']['choices'][0];bc=brow['response']['choices'][0]
        diffs.append(dict(event_id=arow['event_id'],turn=arow['turn'],equal=ac['text']==bc['text'],
            external_hit_tokens=brow['external_hit_tokens'],gpu_hit_tokens=brow['gpu_hit_tokens']))
    write_json(a.output/'summary.json',dict(requests=len(diffs),mismatches=sum(not d['equal'] for d in diffs),details=diffs))
    print(json.dumps({'requests':len(diffs),'mismatches':sum(not d['equal'] for d in diffs)}),flush=True)


if __name__=='__main__':main()
