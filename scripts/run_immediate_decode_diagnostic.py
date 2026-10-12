"""Observe the preserved decode failure with the original immediate connector."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prerequisite', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--stop-starting-at', required=True)
    p.add_argument('--candidate', action='store_true')
    args = p.parse_args()
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        p.error('Future timezone-aware cutoff required')
    import run_protection_ablation as ablation
    import validate_environment as environment
    from run_fixed_baselines import read, token_gate
    from run_arrival_matrix import digest
    from run_reference_baselines import run_plain
    prior = read(args.prerequisite / 'owned-immediate-r0/result.json')
    trace = read(args.prerequisite / 'trace.json')
    if prior['passed'] or digest(trace) != '5deebe7cd1b7ac052ef0010fd870f349f7058675db93d9df20c5bc5f8d9c8cae':
        raise ValueError('Requires original preserved decode192 failure')
    frozen = read(args.prerequisite / 'plan.json')['signature']['source_sha256']
    sources = ablation.source_sha()
    for path in ('scripts/owned_service.py', 'scripts/owned_service_protocol.py',
                 'scripts/owned_service_connector.py', 'scripts/run_reference_baselines.py'):
        if frozen[path] != sources[path]:
            raise ValueError('Original immediate behavior changed: ' + path)
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    environment.write_json(out / 'plan.json', dict(stage='readonly-immediate-decode-diagnostic',
        formal=False, candidate=args.candidate, trace_sha256=digest(trace), source_sha256=sources,
        prerequisite=str(args.prerequisite), cutoff=args.stop_starting_at))
    environment.write_json(out / 'trace.json', trace)
    cell = out / 'owned-immediate-r0'
    original = environment.Service

    class ObservedService(original):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '1'
            os.environ['CACHEPILOT_OWNED_MP_DIR'] = str(cell / 'owned-events')
            if self.log.name == 'lmcache.log':
                self.command = [os.sys.executable, str(ablation.ROOT / 'scripts/immediate_session_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555',
                    '--http-host', '127.0.0.1', '--http-port', '8080',
                    '--l1-size-gb', '16', '--l1-init-size-gb', '16',
                    '--eviction-policy', 'LRU', '--enable-extra-logging']
            elif args.candidate and self.log.name == 'vllm.log':
                index = self.command.index('--kv-transfer-config') + 1
                config = json.loads(self.command[index])
                config.update(kv_connector='ImmediateReceiptConnector',
                    kv_connector_module_path='scripts.immediate_receipt_connector')
                config['kv_connector_extra_config']['cachepilot.immediate_receipt_end'] = True
                self.command[index] = json.dumps(config)
                environment.write_json(cell / 'config.json', config)
                environment.write_json(cell / 'launch-command.json', self.command)
            return super().start(health, timeout)

    environment.Service = ObservedService
    caught = None
    try:
        run_plain(cell, 'owned-immediate', 0, trace)
    except Exception as exc:
        caught = repr(exc)
    finally:
        environment.Service = original
    result = read(cell / 'result.json')
    environment.write_json(out / 'validation.json', dict(formal=False,
        original_resource_gate=result['passed'], tokens=token_gate(cell, trace), caught=caught))
    ablation.ports_free()


if __name__ == '__main__':
    main()
