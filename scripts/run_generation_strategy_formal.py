"""Frozen four-case, three-repeat development comparison; pilot excluded."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import CASES, TRACE_SHA, KV_BYTES, read, digest, strategy_config, strict_idle

ADDITIONS = {'scripts/run_generation_strategy_formal.py', 'scripts/audit_generation_strategy_formal.py',
             'scripts/audit_generation_strategy_pilot.py'}


def order(repeat):
    names = list(CASES)
    return names[repeat:] + names[:repeat]


def check_sources(previous, current):
    changed = [p for p, sha in previous.items() if current.get(p) != sha]
    extra = set(current) - set(previous)
    if changed or extra != ADDITIONS:
        raise ValueError(f'Pilot source mismatch: changed={changed}, additions={sorted(extra)}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pilot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import run_protection_ablation as ablation
    import validate_environment as env
    from audit_generation_strategy_pilot import audit as pilot_audit
    from audit_generation_strategy_formal import audit, audit_one
    if Path(ablation.__file__).resolve() != Path(__file__).with_name('run_protection_ablation.py').resolve():
        raise RuntimeError('Measurement helper outside frozen scripts')
    pilot = read(args.pilot / 'plan.json')
    trace = read(args.pilot / 'trace.json')
    sources = ablation.source_sha()
    check_sources(pilot['signature']['source_sha256'], sources)
    if (pilot['stage'] != 'pilot' or pilot['repeats'] != 1 or pilot['cases'] != CASES
            or digest(trace) != TRACE_SHA or not pilot_audit(args.pilot)['matrix_complete']):
        raise ValueError('Complete independently audited original pilot required')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    signature = dict(source_sha256=sources, trace_sha256=TRACE_SHA, kv_cache_bytes=KV_BYTES,
                     cases=CASES, gate='generation-strategy-development-formal-v1', hotpath_diagnostics=False)
    env.write_json(out / 'trace.json', trace)
    env.write_json(out / 'plan.json', dict(stage='formal', repeats=3, cases=CASES, signature=signature,
        pilot=str(args.pilot), pilot_signature=pilot['signature'], selection=pilot['selection'],
        orders=[order(i) for i in range(3)], scope='Development replication; no held-out or significance claim'))
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
    for repeat in range(3):
        for name in order(repeat):
            if ablation.source_sha() != sources:
                raise RuntimeError('Frozen formal source changed')
            ablation.ports_free()
            current = CASES[name]
            cell = out / f'{name}-r{repeat}'
            print('START ' + cell.name, flush=True)
            try:
                ablation.run_cell(cell, current['mode'], repeat, trace, str(ablation.ROOT / 'models/Qwen3-4B'), KV_BYTES)
                validation = audit_one(cell, name, repeat, signature, trace)
                env.write_json(cell / 'strategy-validation.json', validation)
                if not validation['passed']:
                    raise RuntimeError('Formal cell audit failed')
            except BaseException as exc:
                env.write_json(out / 'stopped.json', dict(cell=cell.name, error=repr(exc)))
                env.write_json(out / 'independent-summary.json', audit(out))
                raise
            env.write_json(out / 'independent-summary.json', audit(out))
            print('DONE ' + cell.name, flush=True)


if __name__ == '__main__':
    main()
