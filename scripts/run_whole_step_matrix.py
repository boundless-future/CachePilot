"""Matched uninstrumented pilot/formal after whole-step KV admission.

Same original anonymous trace, h5/d1, zero/one/two, fresh services per cell.
All pilot cells must pass before three rotated formal repetitions may start.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT / 'scripts'))
from cachepilot.config import WholeStepConfig
from run_fixed_baselines import read, KV_BYTES, strict_idle
from run_arrival_matrix import digest
from run_real_structure_pilot import TRACE_SHA, validate_inputs

MODES = ('zero', 'one', 'two')
CASES = {m: dict(kind='lazy', mode=m) for m in MODES}
ADDITIONS = {'scripts/run_whole_step_matrix.py', 'scripts/audit_whole_step_matrix.py'}


def order(repeat):
    return list(MODES[repeat:] + MODES[:repeat])


def configuration(mode):
    return WholeStepConfig(max_chunks=MODES.index(mode)).kv_transfer_config()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('pilot', 'formal'), required=True)
    parser.add_argument('--prerequisite', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stop-starting-at', required=True)
    args = parser.parse_args()
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        parser.error('Future timezone-aware cutoff required')
    import run_protection_ablation as ablation
    import validate_environment as env
    from audit_whole_step_matrix import audit, audit_one
    from audit_whole_step_mainline import audit as admission_audit
    sources = ablation.source_sha()
    prior = read(args.prerequisite / 'plan.json')
    old_sources = prior['signature']['source_sha256']
    if args.stage == 'pilot':
        changes = {p for p in set(sources) | set(old_sources) if sources.get(p) != old_sources.get(p)}
        if (prior['stage'] != 'kv' or set(prior['modes']) != {'one', 'two'}
                or changes != ADDITIONS or not admission_audit(args.prerequisite)['complete']):
            raise ValueError('Pilot needs both KV modes and unchanged admitted runtime')
    elif (prior['stage'] != 'pilot' or old_sources != sources
            or not audit(args.prerequisite)['matrix_complete']):
        raise ValueError('Formal needs a complete same-source pilot')
    model = ROOT / 'models/Qwen3-4B'
    provenance = validate_inputs(args.input / 'built', args.input / 'source', args.input / 'catalogue.json', model)
    trace = read(args.input / 'built/trace.json')
    if digest(trace) != TRACE_SHA:
        raise ValueError('Original anonymous input required')
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    repeats = 1 if args.stage == 'pilot' else 3
    signature = dict(gate='whole-step-matched-v1', source_sha256=sources, trace_sha256=TRACE_SHA,
        kv_cache_bytes=KV_BYTES, hotpath_diagnostics=False, configs={m: configuration(m) for m in MODES})
    env.write_json(out / 'plan.json', dict(stage=args.stage, signature=signature,
        repeats=repeats, cases=CASES, orders=[order(i) for i in range(repeats)],
        stop_starting_at=args.stop_starting_at, prerequisite=str(args.prerequisite.resolve()),
        primary_metric='TTFT P95; compare every repetition with zero and report all repetitions plus means',
        secondary_metrics=['computed prefill tokens', 'H2D/D2H', 'preemptions', 'protection coverage'],
        scope='Original six-session trace; three descriptive repeats, no held-out/generalized benefit claim'))
    shutil.copytree(args.input, out / 'input')
    shutil.copytree(args.prerequisite, out / 'prerequisite')
    env.write_json(out / 'trace.json', trace)
    env.write_json(out / 'provenance-audit.json', provenance)
    with tarfile.open(out / 'frozen-source.tar.gz', 'w:gz') as archive:
        for name in sources:
            archive.add(ROOT / name, arcname=name)
    current = None
    original_service, original_drained = ablation.Service, ablation.cache_drained

    def save_audit():
        try:
            env.write_json(out / 'independent-summary.json', audit(out))
        except BaseException as exc:
            env.write_json(out / 'audit-error.json', dict(error=repr(exc)))

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
            for mode in order(repeat):
                cell = out / f'{mode}-r{repeat}'
                if datetime.now(timezone.utc) >= cutoff:
                    env.write_json(out / 'stopped.json', dict(next_cell=cell.name, reason='deadline'))
                    return
                if ablation.source_sha() != sources:
                    raise RuntimeError('Frozen matrix source changed')
                ablation.ports_free()
                current = mode
                print('START ' + cell.name, flush=True)
                try:
                    ablation.run_cell(cell, mode, repeat, trace, str(model), KV_BYTES)
                    row = audit_one(cell, mode, repeat, signature, trace)
                    env.write_json(cell / 'validation.json', row)
                    if not row['passed']:
                        raise RuntimeError('Independent matrix audit failed')
                except BaseException as exc:
                    env.write_json(out / 'stopped.json', dict(cell=cell.name, error=repr(exc)))
                    raise
                finally:
                    save_audit()
                print('DONE ' + cell.name, flush=True)
    finally:
        ablation.Service, ablation.cache_drained = original_service, original_drained
        save_audit()


if __name__ == '__main__':
    main()
