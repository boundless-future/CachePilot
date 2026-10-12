"""Four fresh services on frozen anonymous source structure; diagnostic only.

No trace padding or pressure injection. Metric polling is an explicit observer
cost, so these cells are not formal performance repetitions.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

from run_generation_matrix import collect, extra_checks, generation_config
from run_generation_matrix import immediate_receipt_valid
from run_fixed_baselines import read, KV_BYTES
from run_reference_baselines import cases_for, run_plain, resource_gate
from run_arrival_matrix import digest
from audit_real_structure import audit

TRACE_SHA = '445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83'
GAUGES = ('vllm:kv_cache_usage_perc', 'vllm:num_requests_running',
          'vllm:num_requests_waiting')


def validate_inputs(built, source, catalogue, model):
    report = audit(built, source, catalogue)
    if report['trace_sha256'] != TRACE_SHA:
        raise ValueError('Frozen real-structure trace required')
    manifest = read(built / 'manifest.json')
    for name, expected in manifest['tokenizer_sha256'].items():
        if hashlib.sha256((model / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Tokenizer source changed: ' + name)
    return report


def pressure_summary(samples):
    """Missing metrics are unknown, never silently interpreted as zero."""
    from run_protection_ablation import metric
    rows = []
    for sample in samples:
        if 'metrics' not in sample:
            continue
        values = {}
        for name in GAUGES:
            if any(line.startswith(name + '{') or line.startswith(name + ' ')
                   for line in sample['metrics'].splitlines()):
                values[name] = metric(sample['metrics'], name)
        rows.append(values)
    return dict(samples=len(samples), errors=sum('error' in s for s in samples),
        observed_metrics={name: dict(samples=sum(name in r for r in rows),
            peak=max((r[name] for r in rows if name in r), default=None)) for name in GAUGES},
        all_gauges_observed=all(any(name in r for r in rows) for name in GAUGES),
        scope='Sampled API gauges, 0.5s interval; peaks are lower bounds, not allocator snapshots.')


def immediate_supported(result):
    # An absent counter is unknown; the opt-in connector also rejects actual
    # PREEMPTED requests at its scheduler entry point.
    return result['vllm_delta'].get('vllm:num_preemptions_total') == 0


def collect_structural(directory):
    report = collect(directory)
    try:
        provenance = audit(directory / 'input/built', directory / 'input/source',
                           directory / 'input/catalogue.json')
        provenance_valid = (provenance == read(directory / 'provenance-audit.json') and
            provenance['trace_sha256'] == TRACE_SHA == report['signature']['trace_sha256'] and
            read(directory / 'trace.json') == read(directory / 'input/built/trace.json'))
    except (OSError, KeyError, ValueError, AssertionError) as exc:
        provenance_valid = False
        report['provenance_error'] = repr(exc)
    for row in report['rows']:
        checks = row.setdefault('checks', {})
        checks['provenance'] = provenance_valid
        try:
            cell = directory / row['cell']
            pressure = pressure_summary(read(cell / 'pressure-samples.json'))
            checks['pressure_recomputed'] = pressure == read(cell / 'pressure-summary.json')
            checks['pressure_observer'] = pressure['all_gauges_observed'] and pressure['errors'] == 0
            result = read(cell / 'result.json')
            checks['immediate_supported'] = row['case'] != 'owned-immediate' or immediate_supported(result)
            row['pressure'] = pressure
            row['preemptions'] = result['vllm_delta'].get('vllm:num_preemptions_total')
        except (OSError, KeyError, ValueError, TypeError) as exc:
            checks['pressure_recomputed'] = False
            row['structural_error'] = repr(exc)
        row['passed'] = row['passed'] and all(checks.values())
    report['matrix_complete'] = all(r['passed'] for r in report['rows'])
    report['passed_cells'] = sum(r['passed'] for r in report['rows'])
    # This is a diagnostic one-run matrix even when all cells pass.
    report['aggregates'] = {}
    for comparison in report['output_comparisons']:
        comparison['eligible'] = all(r['passed'] for r in report['rows']
            if r['case'] in ('native', comparison['case']))
    report['scope'] = 'Anonymous structure diagnostic; polling overhead; no formal performance aggregates.'
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--built', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--catalogue', type=Path, required=True)
    p.add_argument('--decode-formal', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--stop-starting-at', required=True)
    args = p.parse_args()
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        p.error('Future timezone-aware cutoff required')
    import aiohttp
    import run_protection_ablation as ablation
    import validate_environment as environment
    import replay_trace
    model = ablation.ROOT / 'models/Qwen3-4B'
    provenance = validate_inputs(args.built, args.source_dir, args.catalogue, model)
    prior = collect(args.decode_formal)
    prior_plan = read(args.decode_formal / 'plan.json')
    if (not prior['matrix_complete'] or prior['stage'] != 'formal'
            or not prior['signature'].get('immediate_receipt_end')
            or prior['expected_cells'] != 12 or prior_plan['workload'] != 'decode192'):
        raise ValueError('Complete twelve-cell decode receipt formal required')
    sources = ablation.source_sha()
    # Added CPU/runner files are permitted in this NEW signature, but every
    # file from the successful decode stack must still have identical bytes.
    for name, expected in prior['signature']['source_sha256'].items():
        if sources.get(name) != expected:
            raise ValueError('Successful decode source changed: ' + name)
    cases = cases_for('h5p0-d1', 'unprotected')
    trace = read(args.built / 'trace.json')
    signature = dict(source_sha256=sources, trace_sha256=digest(trace), cases=cases,
        kv_cache_bytes=KV_BYTES, gate='real-structure-diagnostic-v1',
        immediate_receipt_end=True, hotpath_diagnostics=False,
        observer_interval_seconds=0.5, scope='anonymous-open-structure-diagnostic')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    environment.write_json(out / 'trace.json', trace)
    (out / 'input').mkdir()
    shutil.copytree(args.built, out / 'input/built')
    shutil.copytree(args.source_dir, out / 'input/source')
    shutil.copyfile(args.catalogue, out / 'input/catalogue.json')
    environment.write_json(out / 'provenance-audit.json', provenance)
    environment.write_json(out / 'plan.json', dict(stage='pilot', formal=False,
        signature=signature, cases=cases, repeats=1, selection=prior_plan['selection'],
        decode_formal=str(args.decode_formal), cutoff=args.stop_starting_at,
        semantics='All six sessions; original transforms; no pressure augmentation; observer cost enabled.'))
    current = None
    cell = None
    original_service = environment.Service
    original_replay = replay_trace.replay

    async def observed_replay(*a, **kw):
        samples = []
        done = asyncio.Event()

        async def observe():
            loop = asyncio.get_running_loop()
            start = loop.time()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as client:
                while not done.is_set():
                    sample = dict(elapsed_seconds=loop.time() - start)
                    try:
                        async with client.get(ablation.API + '/metrics') as response:
                            response.raise_for_status()
                            sample['metrics'] = await response.text()
                    except Exception as exc:
                        sample['error'] = repr(exc)
                    samples.append(sample)
                    try:
                        await asyncio.wait_for(done.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
        task = asyncio.create_task(observe())
        try:
            return await original_replay(*a, **kw)
        finally:
            done.set()
            await task
            environment.write_json(cell / 'pressure-samples.json', samples)
            environment.write_json(cell / 'pressure-summary.json', pressure_summary(samples))

    class StructuralService(original_service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '0'
            os.environ.pop('CACHEPILOT_HANDOFF_DIR', None)
            os.environ.pop('CACHEPILOT_OWNED_MP_DIR', None)
            if self.log.name == 'lmcache.log':
                self.command = [os.sys.executable,
                    str(ablation.ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555',
                    '--http-host', '127.0.0.1', '--http-port', '8080',
                    '--l1-size-gb', '16', '--l1-init-size-gb', '16', '--eviction-policy', 'LRU']
            elif self.log.name == 'vllm.log' and current['kind'] != 'native':
                index = self.command.index('--kv-transfer-config') + 1
                config = json.loads(self.command[index])
                if current['kind'] == 'lazy':
                    config = generation_config(config, current)
                    os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(self.log.parent / 'generation-summary.json')
                else:
                    config.update(kv_connector='ImmediateReceiptConnector',
                        kv_connector_module_path='scripts.immediate_receipt_connector')
                    config['kv_connector_extra_config']['cachepilot.immediate_receipt_end'] = True
                    if not immediate_receipt_valid(config):
                        raise ValueError('Immediate candidate configuration changed')
                self.command[index] = json.dumps(config)
                environment.write_json(self.log.parent / 'config.json', config)
            environment.write_json(self.log.parent / (self.log.stem + '-command.json'), self.command)
            return super().start(health, timeout)

    ablation.Service = environment.Service = StructuralService
    ablation.replay = replay_trace.replay = observed_replay
    original_drained = ablation.cache_drained
    from run_fixed_baselines import strict_idle
    ablation.cache_drained = lambda s: original_drained(s) and strict_idle(s)
    completed = []
    for name, current in cases.items():
        if datetime.now(timezone.utc) >= cutoff:
            environment.write_json(out / 'stopped-at-cutoff.json', dict(next_cell=name, completed=len(completed)))
            return
        if ablation.source_sha() != sources:
            raise RuntimeError('Frozen structural sources changed')
        ablation.ports_free()
        cell = out / (name + '-r0')
        print('START ' + cell.name, flush=True)
        try:
            result = (ablation.run_cell(cell, 'zero', 0, trace, str(model), KV_BYTES)
                if current['kind'] == 'lazy' else run_plain(cell, name, 0, trace))
            checks = dict(result_passed=result['passed'], resources=resource_gate(current['kind'], result),
                **extra_checks(cell, current, result, trace))
            observed = read(cell / 'pressure-summary.json')
            checks['pressure_observer'] = observed['all_gauges_observed'] and observed['errors'] == 0
            checks['immediate_supported'] = current['kind'] != 'immediate' or immediate_supported(result)
            environment.write_json(cell / 'reference-validation.json', dict(passed=all(checks.values()),
                checks=checks, signature=signature, case=current))
            if not all(checks.values()):
                raise RuntimeError('Structural admission cell failed: ' + cell.name)
        except Exception as exc:
            environment.write_json(out / 'stopped-on-failure.json', dict(cell=cell.name, error=repr(exc),
                completed=len(completed), unrun=[n for n in cases if n not in completed and n != name]))
            raise
        completed.append(name)
        environment.write_json(out / 'completed.json', completed)
        print('DONE ' + cell.name, flush=True)
    report = collect_structural(out)
    environment.write_json(out / 'independent-summary.json', report)
    if not report['matrix_complete']:
        raise RuntimeError('Independent structural pilot audit failed')


if __name__ == '__main__':
    main()
