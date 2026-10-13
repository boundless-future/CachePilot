"""Fixed historical input correctness diagnostic, never performance data."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
import run_protection_ablation as ablation
import validate_environment as env
from run_generation_strategy_pilot import strategy_config, CASES, KV_BYTES, strict_idle, digest, read
from audit_generation_strategy_validation import VALIDATION_SHA


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--windows', action='store_true')
    parser.add_argument('--no-kv', action='store_true', help='Coverage-only diagnostic; no bitwise claim')
    parser.add_argument('--sparse', action='store_true', help='Capture only protected STORE KV and its later loads')
    parser.add_argument('--restore-postlude', action='store_true', help='Separate eviction and protected-prefix restore workload')
    args = parser.parse_args()
    if sum((args.windows, args.no_kv, args.sparse)) > 1:
        parser.error('Choose one diagnostic variant')
    if args.restore_postlude and not args.sparse:
        parser.error('Restore postlude requires sparse KV capture')
    trace = read(args.trace)
    if digest(trace) != VALIDATION_SHA:
        raise ValueError('Historical trace changed')
    if Path(ablation.__file__).resolve() != Path(__file__).with_name('run_protection_ablation.py'):
        raise RuntimeError('Wrong measurement helper')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sources = ablation.source_sha()
    env.write_json(out / 'plan.json', dict(signature=dict(source_sha256=sources),
        trace_sha256=VALIDATION_SHA, kv_cache_bytes=KV_BYTES,
        case=CASES['selected-one'], performance=False, windows=args.windows, no_kv=args.no_kv, sparse=args.sparse,
        restore_postlude=args.restore_postlude,
        scope='Historical new-text trace, unchanged reserve and h5/d1; synchronizing KV probes'))
    cell = out / 'one'

    class Service(env.Service):
        def start(self, health, timeout=900):
            os.environ.update(CACHEPILOT_OWNED_EVENT_LOG='1',
                CACHEPILOT_OWNED_SERVICE_DIR=str(cell / 'events'),
                CACHEPILOT_OWNED_MP_DIR=str(cell / 'events'),
                CACHEPILOT_HANDOFF_DIR=str(cell / 'events'),
                CACHEPILOT_PROBE_DIR=str(cell / 'probe'),
                CACHEPILOT_GENERATION_SUMMARY_PATH=str(cell / 'generation.json'))
            if args.no_kv:
                os.environ.pop('CACHEPILOT_PROBE_DIR', None)
                os.environ.pop('CACHEPILOT_OWNED_MP_DIR', None)
            if self.log.name == 'lmcache.log':
                self.command = [sys.executable, str(ablation.ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555', '--http-host', '127.0.0.1',
                    '--http-port', '8080', '--l1-size-gb', '16', '--l1-init-size-gb', '16',
                    '--eviction-policy', 'LRU', '--enable-extra-logging']
            else:
                i = self.command.index('--kv-transfer-config') + 1
                config = strategy_config(json.loads(self.command[i]), CASES['selected-one'])
                config.update(kv_connector='SharedPrefixProbeConnector',
                              kv_connector_module_path='scripts.shared_prefix_probe')
                config['kv_connector_extra_config']['cachepilot.shared_prefix_protection'] = True
                if args.windows:
                    config.update(kv_connector='SharedPrefixWindowProbeConnector',
                        kv_connector_module_path='scripts.shared_prefix_window_probe')
                if args.no_kv:
                    if args.windows:
                        raise ValueError('Select windows or no-kv, not both')
                    config.update(kv_connector='SharedPrefixConnector',
                        kv_connector_module_path='scripts.shared_prefix_connector')
                if args.sparse:
                    config.update(kv_connector='SharedPrefixSparseProbeConnector',
                        kv_connector_module_path='scripts.shared_prefix_sparse_probe')
                self.command[i] = json.dumps(config)
                env.write_json(cell / 'config.json', config)
                env.write_json(cell / 'launch-command.json', self.command)
            return super().start(health, timeout)

    ablation.Service = Service
    if args.restore_postlude:
        original_replay = ablation.replay
        async def replay_with_postlude(*replay_args, **replay_kwargs):
            import hashlib
            import requests
            from transformers import AutoTokenizer
            from audit_generation_pilot import rows
            result = await original_replay(*replay_args, **replay_kwargs)
            tokenizer = AutoTokenizer.from_pretrained(ablation.ROOT / 'models/Qwen3-4B')
            handoffs = rows(cell / 'events', 'handoff-*.jsonl')
            targets = {r['prefix_sha256']:r for r in handoffs
                if r['event'] == 'scheduler_built' and r['source'] == 'protection'}
            prompts = {}
            for event in trace['events']:
                ids = tokenizer.encode(event['prompt'], add_special_tokens=False)
                key = hashlib.sha256(json.dumps(['', ids[:256]], separators=(',', ':')).encode()).hexdigest()
                if key in targets:
                    prompts[key] = ids[:257]
            if not targets or set(prompts) != set(targets):
                raise RuntimeError('Cannot map protected heads to original input')
            records = []
            def send(label, ids):
                payload = dict(model=str(ablation.ROOT / 'models/Qwen3-4B'), prompt=ids,
                    max_tokens=1, temperature=0, seed=42, ignore_eos=True)
                response = requests.post(ablation.API + '/v1/completions', json=payload, timeout=180)
                response.raise_for_status()
                data = response.json()
                if data['usage']['completion_tokens'] != 1:
                    raise RuntimeError('Incomplete postlude request')
                records.append(dict(label=label, payload=payload, response=data))
                env.write_json(cell / 'restore-postlude.json', dict(targets=targets, requests=records))
            for i in range(160):
                send(f'evict-{i}', tokenizer.encode(f'Sparse-evict-{i}: ' + env.PROMPT)[:224])
            for key, ids in prompts.items():
                send('restore-' + key, ids)
            for i in range(160):
                send(f'final-{i}', tokenizer.encode(f'Sparse-final-{i}: ' + env.PROMPT)[:224])
            return result
        ablation.replay = replay_with_postlude
    original = ablation.cache_drained
    ablation.cache_drained = lambda state: original(state) and strict_idle(state)
    try:
        ablation.run_cell(cell, 'one', 0, trace, str(ablation.ROOT / 'models/Qwen3-4B'), KV_BYTES)
        if ablation.source_sha() != sources:
            raise RuntimeError('Source changed during diagnostic')
        from audit_shared_prefix_trace import audit, coverage_audit
        result = coverage_audit(out) if args.no_kv else audit(out)
        env.write_json(out / 'audit.json', result)
        if not result['passed']:
            raise RuntimeError('Shared-prefix diagnostic failed')
    except BaseException as exc:
        env.write_json(out / 'stopped.json', dict(error=repr(exc)))
        raise
    finally:
        ablation.ports_free()


if __name__ == '__main__':
    main()
