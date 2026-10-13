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
    args = parser.parse_args()
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
        case=CASES['selected-one'], performance=False,
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
                self.command[i] = json.dumps(config)
                env.write_json(cell / 'config.json', config)
                env.write_json(cell / 'launch-command.json', self.command)
            return super().start(health, timeout)

    ablation.Service = Service
    original = ablation.cache_drained
    ablation.cache_drained = lambda state: original(state) and strict_idle(state)
    try:
        ablation.run_cell(cell, 'one', 0, trace, str(ablation.ROOT / 'models/Qwen3-4B'), KV_BYTES)
        if ablation.source_sha() != sources:
            raise RuntimeError('Source changed during diagnostic')
        from audit_shared_prefix_trace import audit
        result = audit(out)
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
