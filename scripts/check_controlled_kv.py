"""Compare cold/GPU-hot/CPU-restored output with identical prefix production inputs."""
import argparse
import json
import os
from pathlib import Path
import socket
import time

import requests
from transformers import AutoTokenizer

from analyze_replay import counters
from validate_environment import ROOT, Service, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--eager', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for port in [8000,5556,8081]:
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1',port)) == 0:
                raise RuntimeError(f'Port {port} occupied')
    os.environ.update(LMCACHE_PORT='5556', LMCACHE_HTTP_PORT='8081', VLLM_SERVER_DEV_MODE='1')
    os.environ['PYTHONPATH'] = str(ROOT/'scripts') + os.pathsep + os.environ.get('PYTHONPATH','')
    api = 'http://127.0.0.1:8000'
    model = str(ROOT/'models/Qwen3-4B')
    tokenizer = AutoTokenizer.from_pretrained(model)
    events = json.loads(args.trace.read_text())['events']
    results = []

    def request(prompt, salt, tokens=48):
        before = counters(requests.get(api+'/metrics',timeout=10).text)
        r = requests.post(api+'/v1/completions', json=dict(model=model,prompt=prompt,
            cache_salt=salt,max_tokens=tokens,temperature=0,seed=42,ignore_eos=True,logprobs=5),timeout=120)
        r.raise_for_status(); data = r.json()
        expected = len(prompt) if isinstance(prompt,list) else len(tokenizer.encode(prompt,add_special_tokens=False))
        assert data['usage']['prompt_tokens'] == expected
        assert data['usage']['completion_tokens'] == tokens
        after = counters(requests.get(api+'/metrics',timeout=10).text)
        return dict(response=data, gpu_hit=after.get('vllm:prefix_cache_hits_total',0)-before.get('vllm:prefix_cache_hits_total',0),
                    cpu_hit=after.get('vllm:external_prefix_cache_hits_total',0)-before.get('vllm:external_prefix_cache_hits_total',0))

    def reset():
        for _ in range(20):
            request('Tick.', 'pump', 8)
            time.sleep(.1)
            r = requests.post(api+'/reset_prefix_cache',timeout=20);r.raise_for_status()
            if r.json()['success']:return
        raise RuntimeError('Could not reset GPU prefix cache')

    for mode in ['baseline', 'immediate']:
        out = args.output/mode;out.mkdir()
        command = ['bash',str(ROOT/'scripts/serve.sh'),mode,'--max-num-seqs','1']
        if args.eager:command.append('--enforce-eager')
        if mode == 'immediate':
            config = json.loads((ROOT/'configs/lmcache-0.5.5-retrieve.json').read_text())
            config.update(kv_connector='DiagnosticConnector',kv_connector_module_path='diagnostic_connector')
            config['kv_connector_extra_config']['lmcache.mp.port'] = 5556
            command += ['--kv-transfer-config',json.dumps(config)]
            os.environ['CACHEPILOT_PROBE_DIR'] = str(out/'probe')
        engine = Service(command,out/'vllm.log')
        cache = Service(['bash',str(ROOT/'scripts/lmcache-server.sh')],out/'lmcache.log')
        try:
            if mode == 'immediate':cache.start('http://127.0.0.1:8081/status')
            engine.start(api+'/health')
            for eid in [8,9]:
                event = next(e for e in events if e['event_id']==eid)
                source = next(e for e in events if e['session_id']==event['session_id'] and e['turn']==0)
                source_ids = tokenizer.encode(source['prompt'],add_special_tokens=False)
                reset()
                cold = request(event['prompt'],f'{mode}-cold-{eid}')
                assert cold['gpu_hit']==cold['cpu_hit']==0
                for source_length in [2048,len(source_ids)]:
                    reset();salt = f'{mode}-e{eid}-n{source_length}'
                    seeded = request(source_ids[:source_length],salt,1)
                    assert seeded['gpu_hit']==seeded['cpu_hit']==0
                    hot = request(event['prompt'],salt)
                    assert hot['gpu_hit']==2048 and hot['cpu_hit']==0, hot
                    restored = None
                    if mode == 'immediate':
                        reset();restored = request(event['prompt'],salt)
                        assert restored['gpu_hit']==0 and restored['cpu_hit']==2048, restored
                    row = dict(mode=mode,event_id=eid,source_length=source_length,
                               cold=cold,seeded=seeded,gpu_hot=hot,cpu_restored=restored)
                    results.append(row);write_json(args.output/'responses.json',results)
                    print(json.dumps(dict(mode=mode,event_id=eid,source_length=source_length,
                        hot_equals_cold=hot['response']['choices'][0]['text']==cold['response']['choices'][0]['text'],
                        cpu_equals_hot=None if restored is None else restored['response']['choices'][0]['text']==hot['response']['choices'][0]['text'])),flush=True)
        finally:
            engine.stop();cache.stop()
    write_json(args.output/'complete.json',dict(eager=args.eager,cases=len(results)))


if __name__=='__main__':main()
