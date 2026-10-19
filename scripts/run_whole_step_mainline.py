"""Frozen anonymous end-to-end coverage experiment; stop at the first failure.

This is diagnostic admission, with handoff/service events, not formal timing.
It reuses the established replay/lifecycle harness without changing old gates.
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
from run_real_structure_pilot import TRACE_SHA, validate_inputs
from run_fixed_baselines import KV_BYTES, strict_idle, read
from run_arrival_matrix import digest

MODES = {'zero': 0, 'one': 1, 'two': 2}


def configuration(mode, stage='coverage'):
    result = WholeStepConfig(max_chunks=MODES[mode]).kv_transfer_config()
    if stage == 'kv':
        result.update(kv_connector='WholeStepSparseProbeConnector',
                      kv_connector_module_path='scripts.whole_step_sparse_probe')
    return result


def restore_postlude(cell, trace, model, tokenizer):
    """Separate from the original replay: evict, then restore captured heads."""
    import hashlib
    import requests
    import validate_environment as env
    from audit_generation_pilot import rows
    targets = {r['batch_id']: r for r in rows(cell / 'handoffs', '*.jsonl')
               if r['event'] == 'worker_bound' and r['source'] == 'protection'}
    if not targets:
        raise RuntimeError('No protected source before correctness postlude')
    records = []
    report = dict(targets=targets, requests=records,
                  scope='Correctness-only postlude; excluded from the original 60-request timing summary')

    def send(label, ids):
        payload = dict(model=model, prompt=ids, max_tokens=1, temperature=0, seed=42, ignore_eos=True)
        response = requests.post('http://127.0.0.1:8000/v1/completions', json=payload, timeout=180)
        data = response.json()
        records.append(dict(label=label, payload=payload, response=data, http_status=response.status_code))
        env.write_json(cell / 'restore-postlude.json', report)
        response.raise_for_status()
        if data['usage']['completion_tokens'] != 1 or data['usage']['prompt_tokens'] != len(ids):
            raise RuntimeError('Incomplete correctness postlude request')

    for index in range(80):
        ids = tokenizer.encode(f'Whole-step-evict-{index}: ' + env.PROMPT, add_special_tokens=False)[:224]
        if len(ids) != 224:
            raise RuntimeError('Unexpected eviction prompt length')
        send(f'evict-{index}', ids)
    for identity, batch in targets.items():
        end = batch['end']
        matches = [e['prompt'][:end + 1] for e in trace['events'] if len(e['prompt']) > end
            and hashlib.sha256(json.dumps(['', e['prompt'][:end]], separators=(',', ':')).encode()).hexdigest()
            == batch['prefix_sha256']]
        if not matches:
            raise RuntimeError('Protected source cannot map to original anonymous tokens')
        send('restore-' + identity, matches[0])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=('coverage', 'kv'), default='coverage')
    parser.add_argument('--admission', type=Path, help='Completed coverage prerequisite for the KV stage')
    parser.add_argument('--modes', nargs='+', choices=tuple(MODES))
    parser.add_argument('--stop-starting-at', required=True)
    args = parser.parse_args()
    if args.modes is None:
        args.modes = ['one', 'two'] if args.stage == 'kv' else ['one', 'two', 'zero']
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        parser.error('Future timezone-aware cutoff required')
    if len(args.modes) != len(set(args.modes)):
        parser.error('Duplicate modes')
    import run_protection_ablation as ablation
    import validate_environment as env
    from audit_whole_step_mainline import audit, audit_one
    model = ROOT / 'models/Qwen3-4B'
    provenance = validate_inputs(args.input / 'built', args.input / 'source', args.input / 'catalogue.json', model)
    trace = read(args.input / 'built/trace.json')
    if digest(trace) != TRACE_SHA:
        raise ValueError('Original anonymous trace required')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model)
    if any(not isinstance(e['prompt'], list) or len(e['prompt']) != e['prompt_tokens']
           or any(type(t) is not int or not 0 <= t < len(tokenizer) for t in e['prompt'])
           for e in trace['events']):
        raise ValueError('Anonymous token contract failed')
    sources = ablation.source_sha()
    prerequisite = None
    if args.stage == 'kv':
        if args.admission is None or 'zero' in args.modes:
            parser.error('KV stage requires --admission and protected modes only')
        prior = read(args.admission / 'plan.json')
        old = prior['signature']['source_sha256']
        allowed = {'scripts/run_whole_step_mainline.py', 'scripts/audit_whole_step_mainline.py',
                   'scripts/whole_step_sparse_probe.py'}
        changed = {p for p in set(old) | set(sources) if old.get(p) != sources.get(p)}
        if (prior['stage'] != 'coverage' or set(prior['modes']) != set(MODES)
                or not audit(args.admission)['complete'] or not changed <= allowed):
            raise ValueError('Complete coverage and unchanged runtime required before KV audit')
        prerequisite = dict(directory=str(args.admission.resolve()), plan=prior,
                            audit=audit(args.admission), allowed_source_changes=sorted(changed))
    ablation.ports_free()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    signature = dict(gate='whole-step-service-' + args.stage + '-v1', source_sha256=sources,
        trace_sha256=TRACE_SHA, kv_cache_bytes=KV_BYTES, hotpath_diagnostics=True,
        configs={m: configuration(m, args.stage) for m in MODES})
    env.write_json(out / 'plan.json', dict(stage=args.stage, signature=signature, prerequisite=prerequisite,
        modes=args.modes, stop_starting_at=args.stop_starting_at,
        correctness_postlude=dict(eviction_requests=80, prompt_tokens=224, max_tokens=1) if args.stage == 'kv' else None,
        scope='Original six-session anonymous replay; diagnostic, not performance or live Agent'))
    shutil.copytree(args.input, out / 'input')
    if args.stage == 'kv':
        shutil.copytree(args.admission, out / 'coverage-admission')
    env.write_json(out / 'trace.json', trace)
    env.write_json(out / 'provenance-audit.json', provenance)
    with tarfile.open(out / 'frozen-source.tar.gz', 'w:gz') as archive:
        for name in sources:
            archive.add(ROOT / name, arcname=name)
    current = None
    original_service, original_drained, original_replay = ablation.Service, ablation.cache_drained, ablation.replay

    def save_audit():
        try:
            env.write_json(out / 'independent-summary.json', audit(out))
        except BaseException as exc:
            env.write_json(out / 'audit-error.json', dict(error=repr(exc)))

    class Service(env.Service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '1'
            os.environ['CACHEPILOT_HANDOFF_DIR'] = str(self.log.parent / 'handoffs')
            os.environ['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT)))
            if args.stage == 'kv':
                os.environ['CACHEPILOT_PROBE_DIR'] = str(self.log.parent / 'probe')
                os.environ['CACHEPILOT_OWNED_MP_DIR'] = str(self.log.parent / 'owned-events')
            if self.log.name == 'lmcache.log':
                self.command = [sys.executable, str(ROOT / 'scripts/generation_service_observer.py'),
                    'server', '--host', '127.0.0.1', '--port', '5555',
                    '--http-host', '127.0.0.1', '--http-port', '8080',
                    '--l1-size-gb', '16', '--l1-init-size-gb', '16', '--eviction-policy', 'LRU']
            else:
                config = configuration(current, args.stage)
                self.command[self.command.index('--kv-transfer-config') + 1] = json.dumps(config)
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH'] = str(self.log.parent / 'generation-summary.json')
                env.write_json(self.log.parent / 'config.json', config)
            env.write_json(self.log.parent / (self.log.stem + '-launch.json'), self.command)
            return super().start(health, timeout)

    ablation.Service = Service
    ablation.cache_drained = lambda state: original_drained(state) and strict_idle(state)
    if args.stage == 'kv':
        async def replay_with_postlude(*replay_args, **replay_kwargs):
            result = await original_replay(*replay_args, **replay_kwargs)
            restore_postlude(out / f'{current}-r0', trace, str(model), tokenizer)
            return result
        ablation.replay = replay_with_postlude
    try:
        for mode in args.modes:
            cell = out / f'{mode}-r0'
            if datetime.now(timezone.utc) >= cutoff:
                env.write_json(out / 'stopped.json', dict(next_cell=cell.name, reason='deadline'))
                break
            if ablation.source_sha() != sources:
                raise RuntimeError('Frozen source changed')
            ablation.ports_free()
            current = mode
            print('START ' + cell.name, flush=True)
            try:
                ablation.run_cell(cell, mode, 0, trace, str(model), KV_BYTES)
                row = audit_one(cell, mode, signature, trace)
                env.write_json(cell / 'validation.json', row)
                if not row['passed']:
                    raise RuntimeError('Independent coverage audit failed')
            except BaseException as exc:
                env.write_json(out / 'stopped.json', dict(cell=cell.name, error=repr(exc)))
                raise
            finally:
                save_audit()
            print('DONE ' + cell.name, flush=True)
            ablation.ports_free()
    finally:
        ablation.Service, ablation.cache_drained = original_service, original_drained
        ablation.replay = original_replay
        save_audit()


if __name__ == '__main__':
    main()
