"""Run unchanged receipt/cancellation workloads with opt-in idle finalization.

All original KV, provenance, pin and resource gates remain in the underlying
smoke runner. No recovery traffic or modified drain timeout is added here.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys


def configure_probe(config, grace):
    if not math.isfinite(grace) or grace <= 0:
        raise ValueError('Diagnostic idle grace must be positive and finite')
    names = {'ReceiptProbeConnector': 'IdleReceiptProbeConnector',
             'SharedCancelProbeConnector': 'IdleSharedCancelProbeConnector'}
    if config['kv_connector'] not in names:
        raise ValueError('Unsupported idle diagnostic composition')
    result = deepcopy(config)
    result['kv_connector'] = names[config['kv_connector']]
    result['kv_connector_module_path'] = 'scripts.idle_finalize_probe_connector'
    result['kv_connector_extra_config'].update({
        'cachepilot.idle_finalize': True,
        'cachepilot.idle_finalize_grace_seconds': grace})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=('delayed', 'failed-after-success', 'shared-cancel'), required=True)
    parser.add_argument('--grace-seconds', type=float, default=15)
    args = parser.parse_args()
    if not math.isfinite(args.grace_seconds) or args.grace_seconds <= 0:
        parser.error('Grace must be positive and finite')
    import protection_zero_smoke as smoke
    from validate_environment import Service, write_json

    class IdleService(Service):
        def start(self, health, timeout=900):
            if self.log.name == 'vllm.log':
                index = self.command.index('--kv-transfer-config') + 1
                config = configure_probe(json.loads(self.command[index]), args.grace_seconds)
                self.command[index] = json.dumps(config)
                write_json(self.log.parent / 'config.json', config)
                sources = ('idle_finalize_smoke.py', 'idle_finalize_probe_connector.py',
                           'idle_finalize.py', 'idle_finalize_connector.py',
                           'protection_zero_smoke.py', 'protection_receipt_probe.py',
                           'protection_shared_cancel_probe.py')
                write_json(self.log.parent / 'idle-diagnostic-plan.json', dict(
                    case=args.case, grace_seconds=args.grace_seconds,
                    source_sha256={name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                   for name in sources},
                    recovery_traffic=False, unchanged_workload=True,
                    unchanged_drain_timeout_seconds=20,
                    scope='Diagnostic composition; not performance or actual DMA failure'))
            return super().start(health, timeout)

    smoke.Service = IdleService
    sys.argv = ['protection_zero_smoke.py', '--output', str(args.output.resolve()),
                '--bounded-workload', '--modes', 'two']
    sys.argv += (['--shared-cancel'] if args.case == 'shared-cancel'
                 else ['--receipt-probe', args.case])
    smoke.main()
    cell = args.output / 'two'
    rows = [json.loads(line) for p in (cell / 'events').glob('handoff-*.jsonl')
            for line in p.read_text(encoding='utf-8').splitlines()]
    drops = [r for r in rows if r['event'] == 'idle_pending_dropped']
    result = json.loads((cell / 'result.json').read_text(encoding='utf-8'))
    write_json(cell / 'idle-coverage.json', dict(
        original_smoke_passed=result['passed'], actual_idle_drop=bool(drops),
        drops=drops, interpretation=('Joint idle-drop and probe coverage' if drops else
                                    'Compatibility only; idle-drop path not covered')))


if __name__ == '__main__':
    main()
