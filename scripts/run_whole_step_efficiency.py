"""One matched hypothesis: skip scans when no actionable STORE exists.

Run pilot before formal, or audit archived evidence without GPU libraries.
The old matrix and its admission gates remain independently replayable.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT / 'scripts'))
from cachepilot.config import WholeStepConfig
from run_fixed_baselines import read, KV_BYTES, strict_idle
from run_real_structure_pilot import TRACE_SHA, validate_inputs
from run_arrival_matrix import digest

LABELS = ('zero', 'legacy-two', 'filtered-two')
ALLOWED = {'src/cachepilot/runtime/shared_prefix.py',
    'src/cachepilot/integration/vllm/whole_step.py', 'scripts/audit_whole_step_matrix.py',
    'scripts/analyze_whole_step_net_effect.py', 'scripts/run_whole_step_efficiency.py'}


def configuration(label):
    config = WholeStepConfig(max_chunks=0 if label == 'zero' else 2).kv_transfer_config()
    if label == 'filtered-two':
        config['kv_connector_extra_config']['cachepilot.skip_ineligible'] = True
    return config


def order(repeat):
    return list(LABELS[repeat:] + LABELS[:repeat])


def prerequisite(directory, stage, sources):
    from audit_whole_step_matrix import audit as old_audit
    prior = read(directory / 'plan.json')
    old = prior['signature']['source_sha256']
    if stage == 'pilot':
        changes = {p for p in set(old) | set(sources) if old.get(p) != sources.get(p)}
        if prior['stage'] != 'formal' or not changes <= ALLOWED or not old_audit(directory)['matrix_complete']:
            raise ValueError('Complete original formal and only declared source changes required')
    elif (prior['stage'] != 'pilot' or old != sources or not audit(directory)['formal_eligible']):
        raise ValueError('Same-source complete efficiency pilot with the overhead gate required')


def audit(directory):
    from audit_whole_step_matrix import audit_one
    directory = Path(directory)
    plan, trace = read(directory / 'plan.json'), read(directory / 'trace.json')
    stage, signature = plan['stage'], plan['signature']
    repeats = 1 if stage == 'pilot' else 3
    if (stage not in ('pilot', 'formal') or plan['repeats'] != repeats
            or plan['orders'] != [order(i) for i in range(repeats)]
            or signature['gate'] != 'whole-step-eligibility-v1'
            or signature['configs'] != {label: configuration(label) for label in LABELS}
            or signature['hotpath_diagnostics'] is not False or digest(trace) != TRACE_SHA):
        raise ValueError('Invalid eligibility comparison plan')
    with tarfile.open(directory / 'frozen-source.tar.gz') as archive:
        frozen = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                  for m in archive.getmembers() if m.isfile()}
    if frozen != signature['source_sha256']:
        raise ValueError('Source freeze mismatch')
    prerequisite(directory / 'prerequisite', stage, frozen)
    if read(directory / 'input/built/trace.json') != trace:
        raise ValueError('Input trace differs')
    from audit_real_structure import audit as input_audit
    provenance = input_audit(directory / 'input/built', directory / 'input/source', directory / 'input/catalogue.json')
    if provenance != read(directory / 'provenance-audit.json'):
        raise ValueError('Input provenance differs')
    rows, differences = [], []
    for repeat in range(repeats):
        for label in LABELS:
            cell = directory / f'{label}-r{repeat}'
            mode = 'zero' if label == 'zero' else 'two'
            config = configuration(label)
            cell_signature = dict(signature, configs={mode: config})
            try:
                row = audit_one(cell, mode, repeat, cell_signature, trace, expected_config=config)
                timing = {}
                for p in (cell / 'protection-summary').glob('*.json'):
                    data = read(p)
                    if data['role'] == 'SCHEDULER':
                        timing = data.get('timings', {}).get('prepare', {})
                if label != 'zero' and (timing.get('calls', 0) <= 0 or timing.get('wall_ns', 0) <= 0):
                    raise ValueError('Missing prepare timing')
                row['prepare_wall_ns'] = timing.get('wall_ns', 0)
                row['prepare_calls'] = timing.get('calls', 0)
            except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
                row = dict(passed=False, error=repr(exc))
            rows.append(dict(label=label, repeat=repeat, **row))
        base = next(r for r in rows if r['repeat'] == repeat and r['label'] == 'zero')
        for row in (r for r in rows if r['repeat'] == repeat and 'output_hashes' in r):
            if 'output_hashes' in base:
                differences.append(dict(label=row['label'], repeat=repeat,
                    event_ids=[k for k,v in base['output_hashes'].items() if row['output_hashes'].get(k) != v]))
    complete = all(r['passed'] for r in rows)
    ratios, aggregates = [], {}
    if complete:
        for repeat in range(repeats):
            old = next(r for r in rows if r['repeat'] == repeat and r['label'] == 'legacy-two')
            new = next(r for r in rows if r['repeat'] == repeat and r['label'] == 'filtered-two')
            ratios.append(new['prepare_wall_ns'] / old['prepare_wall_ns'])
        for label in LABELS:
            points = [r for r in rows if r['label'] == label]
            aggregates[label] = {key: dict(individual=[r[key] for r in points], mean=statistics.mean(r[key] for r in points))
                for key in ('ttft_p95_ms', 'prepare_wall_ns', 'prefill_tokens', 'h2d_bytes', 'd2h_bytes', 'preemptions')}
        for label in ('legacy-two', 'filtered-two'):
            aggregates[label]['relative_zero_mean_pct'] = 100 * (aggregates[label]['ttft_p95_ms']['mean']
                / aggregates['zero']['ttft_p95_ms']['mean'] - 1)
        aggregates['filtered-two']['relative_legacy_mean_pct'] = 100 * (
            aggregates['filtered-two']['ttft_p95_ms']['mean'] / aggregates['legacy-two']['ttft_p95_ms']['mean'] - 1)
    return dict(complete=complete, passed_cells=sum(r['passed'] for r in rows), rows=rows,
        prepare_wall_ratio_new_old=ratios, formal_eligible=complete and all(r <= 0.5 for r in ratios),
        aggregates=aggregates, output_differences=differences,
        scope='One fixed workload; overhead hypothesis, no generalized or native/default benefit claim')


def run(args):
    import run_protection_ablation as ablation
    import validate_environment as env
    from audit_whole_step_matrix import audit_one
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        raise ValueError('Future timezone-aware cutoff required')
    sources = ablation.source_sha()
    prerequisite(args.prerequisite, args.stage, sources)
    model = ROOT / 'models/Qwen3-4B'
    provenance = validate_inputs(args.input / 'built', args.input / 'source', args.input / 'catalogue.json', model)
    trace = read(args.input / 'built/trace.json')
    if digest(trace) != TRACE_SHA:
        raise ValueError('Original input required')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.stage == 'pilot' else 3
    signature = dict(gate='whole-step-eligibility-v1', source_sha256=sources, trace_sha256=TRACE_SHA,
        kv_cache_bytes=KV_BYTES, hotpath_diagnostics=False, configs={label: configuration(label) for label in LABELS})
    env.write_json(out / 'plan.json', dict(stage=args.stage, repeats=repeats, signature=signature,
        orders=[order(i) for i in range(repeats)], prerequisite=str(args.prerequisite.resolve()),
        stop_starting_at=args.stop_starting_at, overhead_gate='filtered/legacy prepare wall <= 0.5',
        primary_metric='TTFT P95, retain all paired comparisons; three descriptive repeats'))
    shutil.copytree(args.input, out / 'input')
    shutil.copytree(args.prerequisite, out / 'prerequisite')
    env.write_json(out / 'trace.json', trace)
    env.write_json(out / 'provenance-audit.json', provenance)
    with tarfile.open(out / 'frozen-source.tar.gz', 'w:gz') as archive:
        for name in sources:
            archive.add(ROOT / name, arcname=name)
    current = None
    original_service, original_drained = ablation.Service, ablation.cache_drained

    class Service(env.Service):
        def start(self, health, timeout=900):
            os.environ['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT)))
            if self.log.name == 'lmcache.log':
                self.command = [sys.executable, str(ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555', '--http-host', '127.0.0.1',
                    '--http-port', '8080', '--l1-size-gb', '16', '--l1-init-size-gb', '16', '--eviction-policy', 'LRU']
            else:
                config = configuration(current)
                self.command[self.command.index('--kv-transfer-config') + 1] = json.dumps(config)
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(self.log.parent / 'generation-summary.json')
                env.write_json(self.log.parent / 'config.json', config)
            env.write_json(self.log.parent / (self.log.stem + '-launch.json'), self.command)
            return super().start(health, timeout)

    ablation.Service = Service
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle(state)
    try:
        for repeat in range(repeats):
            for label in order(repeat):
                current = label
                if datetime.now(timezone.utc) >= cutoff:
                    env.write_json(out / 'stopped.json', dict(next_cell=f'{label}-r{repeat}', reason='cutoff'))
                    return
                if ablation.source_sha() != sources:
                    raise RuntimeError('Frozen source changed')
                mode = 'zero' if label == 'zero' else 'two'
                cell = out / f'{label}-r{repeat}'
                ablation.ports_free()
                print('START ' + cell.name, flush=True)
                ablation.run_cell(cell, mode, repeat, trace, str(model), KV_BYTES)
                row = audit_one(cell, mode, repeat, dict(signature, configs={mode: configuration(label)}), trace,
                                expected_config=configuration(label))
                env.write_json(cell / 'validation.json', row)
                if not row['passed']:
                    raise RuntimeError('Cell admission failed')
                print('DONE ' + cell.name, flush=True)
    except BaseException as exc:
        env.write_json(out / 'stopped.json', dict(cell=current, error=repr(exc)))
        raise
    finally:
        ablation.Service, ablation.cache_drained = original_service, original_drained
        try:
            env.write_json(out / 'independent-summary.json', audit(out))
        except BaseException as exc:
            env.write_json(out / 'audit-error.json', dict(error=repr(exc)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'audit'))
    parser.add_argument('--stage', choices=('pilot', 'formal'))
    parser.add_argument('--prerequisite', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stop-starting-at')
    args = parser.parse_args()
    if args.command == 'audit':
        if args.directory is None:
            parser.error('--directory required')
        result = audit(args.directory)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({k: result[k] for k in ('complete', 'passed_cells', 'formal_eligible', 'prepare_wall_ratio_new_old', 'aggregates')}))
        raise SystemExit(0 if result['complete'] else 1)
    if any(getattr(args, key) is None for key in ('stage', 'prerequisite', 'input', 'stop_starting_at')):
        parser.error('run requires stage/prerequisite/input/stop-starting-at')
    run(args)
