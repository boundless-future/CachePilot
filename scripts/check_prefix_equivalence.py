"""Diagnose serial output differences using an equal-length native GPU prefix."""
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
    p = argparse.ArgumentParser()
    p.add_argument('--trace', type=Path, required=True)
    p.add_argument('--regression', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1', 8000)) == 0:
            raise RuntimeError('Port 8000 occupied')
    os.environ['VLLM_SERVER_DEV_MODE'] = '1'
    model = str(ROOT / 'models/Qwen3-4B')
    tokenizer = AutoTokenizer.from_pretrained(model)
    trace = json.loads(args.trace.read_text())
    cold = json.loads((args.regression / 'baseline/responses.json').read_text())
    cpu = json.loads((args.regression / 'immediate/responses.json').read_text())
    targets = [(a, b) for a, b in zip(cold, cpu)
               if a['response']['choices'][0]['text'] != b['response']['choices'][0]['text']]
    api = 'http://127.0.0.1:8000'
    engine = Service(['bash', str(ROOT / 'scripts/serve.sh'), 'baseline',
                      '--max-num-seqs', '1'], args.output / 'vllm.log')
    rows = []
    try:
        engine.start(api + '/health')
        for cold_row, cpu_row in targets:
            event = next(e for e in trace['events'] if e['event_id'] == cold_row['event_id'])
            prefix_len = int(cpu_row['external_hit_tokens'])
            assert prefix_len > 0 and cpu_row['gpu_hit_tokens'] == 0
            ids = tokenizer.encode(event['prompt'], add_special_tokens=False)
            time.sleep(.2)
            reset = requests.post(api + '/reset_prefix_cache', timeout=20)
            reset.raise_for_status()
            assert reset.json()['success']
            # The 1-token response leaves exactly prefix_len input tokens computed.
            warm = requests.post(api + '/v1/completions', json=dict(
                model=model, prompt=ids[:prefix_len], max_tokens=1,
                temperature=0, seed=42, ignore_eos=True), timeout=90)
            warm.raise_for_status()
            before = counters(requests.get(api + '/metrics', timeout=10).text)
            result = requests.post(api + '/v1/completions', json=dict(
                model=model, prompt=event['prompt'], max_tokens=48,
                temperature=0, seed=42, ignore_eos=True, logprobs=5), timeout=90)
            result.raise_for_status()
            after = counters(requests.get(api + '/metrics', timeout=10).text)
            hit = after.get('vllm:prefix_cache_hits_total', 0) - before.get('vllm:prefix_cache_hits_total', 0)
            assert hit == prefix_len, f'GPU prefix {hit} differs from CPU prefix {prefix_len}'
            response = result.json()
            assert response['usage']['prompt_tokens'] == event['prompt_tokens']
            assert response['usage']['completion_tokens'] == 48
            text = response['choices'][0]['text']
            rows.append(dict(event_id=event['event_id'], gpu_hit_tokens=hit,
                             equals_cold=text == cold_row['response']['choices'][0]['text'],
                             equals_cpu=text == cpu_row['response']['choices'][0]['text'], response=response))
            write_json(args.output / 'responses.json', rows)
    finally:
        engine.stop()
    summary = [{k: v for k, v in r.items() if k != 'response'} for r in rows]
    write_json(args.output / 'summary.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
