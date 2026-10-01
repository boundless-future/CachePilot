"""Cold vs CPU-retrieved decode, with and without the synchronizing KV probe.

Not a performance benchmark. Each case uses an isolated cache salt; the paired
requests have identical prompts and reset the GPU prefix cache before each run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time

import requests
from analyze_kv_probe import compare_outputs, summarize_probe
from analyze_replay import counters
from validate_environment import ROOT, Service, write_json


def reset_gpu(api, model):
    for _ in range(20):
        requests.post(api + '/v1/completions', json=dict(model=model, prompt='Tick.',
            max_tokens=8, temperature=0, ignore_eos=True), timeout=60).raise_for_status()
        time.sleep(.1)
        result = requests.post(api + '/reset_prefix_cache', timeout=20)
        result.raise_for_status()
        if result.json()['success']:
            return
    raise RuntimeError('GPU cache reset did not drain')


def analyze(directory):
    comparisons = {}
    modes = {}
    for mode in ('immediate', 'probe'):
        rows = json.loads((directory / mode / 'responses.json').read_text())
        modes[mode] = rows
        cold = [r for r in rows if r['pair'] == 'cold']
        warm = [r for r in rows if r['pair'] == 'retrieve']
        comparisons[mode] = compare_outputs(cold, warm)
        if any(r['external_hit_tokens'] != 0 for r in cold):
            raise RuntimeError('Cold request unexpectedly hit CPU cache')
        if any(r['external_hit_tokens'] <= 0 or r['gpu_hit_tokens'] != 0 for r in warm):
            raise RuntimeError('Paired request did not use CPU retrieval alone')
    for pair in ('cold', 'retrieve'):
        comparisons['probe_interference_' + pair] = compare_outputs(
            [r for r in modes['immediate'] if r['pair'] == pair],
            [r for r in modes['probe'] if r['pair'] == pair])
    kv = [json.loads(line) for p in sorted((directory / 'probe/probe').glob('kv-*.jsonl'))
          for line in p.read_text().splitlines()]
    responses = modes['probe']
    # OpenAI IDs are the prefix of worker request IDs in this pinned version.
    request_pairs = {r['response']['id']: r['pair'] for r in responses}
    decode = []
    for row in kv:
        if row['phase'] != 'decode':
            continue
        pair = next((v for k, v in request_pairs.items() if row['request_id'].startswith(k)), None)
        if pair == 'retrieve':
            decode.append(row)
    expected = len(responses) // 2 * 47
    per_request = {}
    missing, unequal, equal = [], [], 0
    for row in decode:
        per_request.setdefault(row['request_id'], []).append(row['decode_index'])
        for layer in row['layers']:
            detail = dict(request_id=row['request_id'], decode_index=row['decode_index'],
                layer=layer['layer'], prefix=row['token_prefix_sha256'])
            if not layer.get('reference_present'):
                missing.append(detail)
            elif layer['bitwise_equal']:
                equal += 1
            else:
                unequal.append(dict(**detail, max_abs_difference=layer['max_abs_difference'],
                    different_elements=layer['different_elements']))
    coverage = (len(per_request) == len(responses)//2 and len(decode) == expected
                and all(sorted(indices) == list(range(1, 48)) for indices in per_request.values())
                and all(len(row['layers']) == 36 for row in decode))
    result = dict(outputs=comparisons, restored_kv=summarize_probe(kv), decode=dict(
        expected_tokens=expected, observed_tokens=len(decode), coverage=coverage,
        equal_layers=equal, missing_prefix_references=missing, unequal_layers=unequal,
        covered_and_bitwise_equal=coverage and not missing and not unequal))
    write_json(directory / 'analysis.json', result)
    print(json.dumps(dict(outputs={k:v['mismatches'] for k,v in comparisons.items()},
        decode_tokens=len(decode), coverage=coverage, equal_layers=equal,
        missing_layers=len(missing), unequal_layers=len(unequal))), flush=True)
    if not coverage:
        raise RuntimeError('Decode snapshot coverage incomplete')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--trace', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--execution', choices=['eager', 'compile-only', 'graph-only'], required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    for port in (8000, 5556, 8081):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'Port {port} occupied')
    trace = json.loads(a.trace.read_text())
    cases = [trace['events'][i] for i in (0, 4, 8, 9)]
    compilation = {'compile-only':dict(mode=3, cudagraph_mode='NONE'),
                   'graph-only':dict(mode=0, cudagraph_mode='FULL_DECODE_ONLY')}.get(a.execution)
    write_json(a.output / 'manifest.json', dict(execution=a.execution,
        compilation_config=compilation, event_ids=[e['event_id'] for e in cases],
        trace_sha256=hashlib.sha256(a.trace.read_bytes()).hexdigest(),
        decode_tokens=48, snapshots='every computed generated input token (1..47)',
        max_num_seqs=1, async_scheduling=False, gpu_reset_before_each_request=True))
    os.environ.update(LMCACHE_PORT='5556', LMCACHE_HTTP_PORT='8081', VLLM_SERVER_DEV_MODE='1')
    api = 'http://127.0.0.1:8000'
    cache_url = 'http://127.0.0.1:8081'
    model = str(ROOT / 'models/Qwen3-4B')
    for mode in ('immediate', 'probe'):
        out = a.output / mode
        out.mkdir()
        command = ['bash', str(ROOT / 'scripts/serve.sh'), 'immediate', '--max-num-seqs', '1',
                   '--no-async-scheduling']
        if compilation:
            command += ['--compilation-config', json.dumps(compilation)]
        else:
            command += ['--enforce-eager']
        os.environ['CACHEPILOT_DECODE_PROBE'] = '0'
        if mode == 'probe':
            config = json.loads((ROOT / 'configs/lmcache-0.5.5-retrieve.json').read_text())
            config.update(kv_connector='DiagnosticConnector', kv_connector_module_path='diagnostic_connector')
            config['kv_connector_extra_config']['lmcache.mp.port'] = 5556
            command += ['--kv-transfer-config', json.dumps(config)]
            os.environ['PYTHONPATH'] = str(ROOT / 'scripts') + os.pathsep + os.environ.get('PYTHONPATH', '')
            os.environ['CACHEPILOT_PROBE_DIR'] = str(out / 'probe')
            os.environ['CACHEPILOT_DECODE_PROBE'] = '1'
        engine = Service(command, out / 'vllm.log')
        cache = Service(['bash', str(ROOT / 'scripts/lmcache-server.sh')], out / 'lmcache.log')
        rows = []
        try:
            cache.start(cache_url + '/status')
            engine.start(api + '/health')
            for case in cases:
                for pair in ('cold', 'retrieve'):
                    reset_gpu(api, model)
                    before = counters(requests.get(api + '/metrics', timeout=10).text)
                    response = requests.post(api + '/v1/completions', json=dict(model=model,
                        prompt=case['prompt'], max_tokens=48, temperature=0, seed=42,
                        ignore_eos=True, logprobs=5, cache_salt='decode-case-' + str(case['event_id'])), timeout=90)
                    response.raise_for_status()
                    data = response.json()
                    if data['usage']['completion_tokens'] != 48:
                        raise RuntimeError('Decode length mismatch')
                    after = counters(requests.get(api + '/metrics', timeout=10).text)
                    rows.append(dict(event_id=case['event_id'], turn=case['turn'], pair=pair,
                        response=data,
                        external_hit_tokens=after.get('vllm:external_prefix_cache_hits_total',0)-before.get('vllm:external_prefix_cache_hits_total',0),
                        gpu_hit_tokens=after.get('vllm:prefix_cache_hits_total',0)-before.get('vllm:prefix_cache_hits_total',0)))
                    write_json(out / 'responses.json', rows)
            print(f'DONE {mode}: {len(rows)} paired requests', flush=True)
        finally:
            engine.stop()
            cache.stop()
    analyze(a.output)


if __name__ == '__main__':
    main()
