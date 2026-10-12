"""New-stack normal-path GPU admission using the unchanged bounded workload."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import protection_zero_smoke as smoke
    from run_protection_ablation import ports_free, source_sha
    from audit_generation_pilot import audit_rows, rows
    from validate_environment import Service, ROOT, write_json
    ports_free()
    original_state = smoke.cache_state
    def observed_state(api):
        import requests
        result = original_state(api)
        response = requests.get(api + '/status', timeout=10)
        response.raise_for_status()
        result['generation_observer'] = response.json()['generation_observer']
        return result
    smoke.cache_state = observed_state
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen = source_sha()
    write_json(out / 'plan.json', dict(stage='generation-protection-normal-diagnostic',
        modes=['zero', 'one', 'two'], source_sha256=frozen,
        kv_cache_bytes=2147483648, model=str(ROOT / 'models/Qwen3-4B'),
        workload='unchanged protection_zero_smoke bounded_workload, 576-token source',
        performance=False, fault_injection=False,
        gates=['existing strict KV/ownership/handoff', 'exact generation END',
               'server sessions empty', 'new-stack snapshot closed']))
    results = []
    for mode in ('zero', 'one', 'two'):
        if source_sha() != frozen:
            raise RuntimeError('Source changed during diagnostic')
        cell = out / mode

        class DiagnosticService(Service):
            def start(self, health, timeout=900):
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(cell / 'generation.json')
                if self.log.name == 'lmcache.log':
                    self.command = [os.sys.executable, str(ROOT / 'scripts/generation_service_observer.py'),
                        'server', '--host', '127.0.0.1', '--port', '5555', '--http-host', '127.0.0.1',
                        '--http-port', '8080', '--l1-size-gb', '16', '--l1-init-size-gb', '16',
                        '--eviction-policy', 'LRU', '--enable-extra-logging']
                elif self.log.name == 'vllm.log':
                    i = self.command.index('--kv-transfer-config') + 1
                    config = json.loads(self.command[i])
                    config.update(kv_connector='GenerationProtectionProbeConnector',
                        kv_connector_module_path='scripts.generation_protection_probe')
                    config['kv_connector_extra_config'].update({
                        'cachepilot.generation_ledger': True, 'cachepilot.generation_protection': True})
                    self.command[i] = json.dumps(config)
                    write_json(cell / 'config.json', config)
                return super().start(health, timeout)

        smoke.Service = DiagnosticService
        caught = None
        try:
            smoke.run(cell, mode, bounded_workload=True)
        except Exception as exc:
            caught = repr(exc)
        result = json.loads((cell / 'result.json').read_text())
        generation = audit_rows(rows(cell / 'events', 'handoff-*.jsonl'),
            rows(cell / 'events', 'owned-service-*.jsonl'), dict(
                status_before_engine_stop=result.get('running_final', {}),
                status_after_engine_stop=result.get('after_engine_stop', {})))
        snapshot = json.loads((cell / 'generation.json').read_text()) if (cell / 'generation.json').exists() else {}
        passed = bool(result.get('passed') and generation['passed'] and snapshot
            and snapshot.get('protection_poisoned') is None and not snapshot.get('shutdown_error'))
        validation = dict(mode=mode, passed=passed, generation=generation,
            snapshot=snapshot, caught=caught, scope='normal GPU admission only; no fault or performance claim')
        write_json(cell / 'generation-validation.json', validation)
        results.append(validation)
        write_json(out / 'summary.json', results)
        ports_free()
        if not passed:
            raise SystemExit(f'{mode} failed; later modes not started')


if __name__ == '__main__':
    main()
