"""Frozen historical-input coverage pilot; no runtime policy changes."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import CASES, TRACE_SHA, KV_BYTES, read, digest, strategy_config, strict_idle

ADDITIONS = {'scripts/run_generation_strategy_validation.py', 'scripts/audit_generation_strategy_validation.py'}


def check_sources(previous, current):
    if any(current.get(p) != sha for p,sha in previous.items()) or set(current)-set(previous) != ADDITIONS:
        raise ValueError('Only two validation harness additions permitted; runtime must remain unchanged')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--development', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import run_protection_ablation as ablation
    import validate_environment as env
    from audit_generation_strategy_formal import audit as development_audit, audit_one
    from audit_generation_strategy_validation import audit, input_audit, VALIDATION_SHA
    from run_generation_strategy_formal import order
    if Path(ablation.__file__).resolve() != Path(__file__).with_name('run_protection_ablation.py').resolve():
        raise RuntimeError('Measurement helper outside frozen scripts')
    development = read(args.development / 'plan.json')
    development_trace = read(args.development / 'trace.json')
    trace = read(args.trace)
    inputs = input_audit(development_trace, trace)
    sources = ablation.source_sha()
    check_sources(development['signature']['source_sha256'], sources)
    if not inputs['passed'] or not development_audit(args.development)['matrix_complete']:
        raise ValueError('Complete development matrix and unchanged validation inputs required')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(ablation.ROOT / 'models/Qwen3-4B'))
    actual = [len(tokenizer.encode(e['prompt'], add_special_tokens=False)) for e in trace['events']]
    if actual != [e['prompt_tokens'] for e in trace['events']]:
        raise ValueError('Actual tokenizer input lengths differ from frozen trace')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    signature = dict(source_sha256=sources, trace_sha256=VALIDATION_SHA, kv_cache_bytes=KV_BYTES,
                     cases=CASES, gate='generation-strategy-validation-pilot-v1', hotpath_diagnostics=False)
    env.write_json(out / 'trace.json', trace)
    env.write_json(out / 'development-trace.json', development_trace)
    env.write_json(out / 'tokenizer-audit.json', dict(passed=True, prompt_lengths=actual,
        prompt_tokens=sum(actual), model=str(ablation.ROOT / 'models/Qwen3-4B')))
    env.write_json(out / 'plan.json', dict(stage='pilot', repeats=1, cases=CASES, signature=signature,
        development=str(args.development), development_signature=development['signature'],
        input_audit=inputs, orders=[order(0)], scope='Historical input coverage pilot; not new tuning'))
    current = None
    class StrategyService(env.Service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '0'
            if self.log.name == 'lmcache.log':
                self.command = [sys.executable, str(ablation.ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555', '--http-host', '127.0.0.1',
                    '--http-port', '8080', '--l1-size-gb', '16', '--l1-init-size-gb', '16', '--eviction-policy', 'LRU']
            elif self.log.name == 'vllm.log':
                i = self.command.index('--kv-transfer-config') + 1
                config = strategy_config(json.loads(self.command[i]), current)
                self.command[i] = json.dumps(config)
                env.write_json(self.log.parent / 'config.json', config)
                env.write_json(self.log.parent / 'launch-command.json', self.command)
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(self.log.parent / 'generation-summary.json')
            return super().start(health, timeout)
    ablation.Service = StrategyService
    original_drained = ablation.cache_drained
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle(state)
    for repeat in range(1):
        for name in order(repeat):
            if ablation.source_sha() != sources:
                raise RuntimeError('Frozen validation source changed')
            ablation.ports_free()
            current = CASES[name]
            cell = out / f'{name}-r{repeat}'
            print('START ' + cell.name, flush=True)
            try:
                ablation.run_cell(cell, current['mode'], repeat, trace, str(ablation.ROOT / 'models/Qwen3-4B'), KV_BYTES)
                validation = audit_one(cell, name, repeat, signature, trace)
                env.write_json(cell / 'strategy-validation.json', validation)
                if not validation['passed']:
                    raise RuntimeError('Validation cell audit failed')
            except BaseException as exc:
                env.write_json(out / 'stopped.json', dict(cell=cell.name, error=repr(exc)))
                env.write_json(out / 'independent-summary.json', audit(out))
                raise
            env.write_json(out / 'independent-summary.json', audit(out))
            print('DONE ' + cell.name, flush=True)


if __name__ == '__main__':
    main()
