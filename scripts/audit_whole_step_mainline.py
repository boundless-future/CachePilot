"""Offline source/trace/receipt/ownership audit of whole-step coverage cells."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT / 'scripts'))
from cachepilot.config import WholeStepConfig
from run_fixed_baselines import read, KV_BYTES, token_gate
from run_arrival_matrix import digest
from run_real_structure_pilot import TRACE_SHA
from run_generation_matrix import generation_closed
from run_reference_baselines import resource_gate
from audit_replay_measurements import audit_cell as timing_audit
from audit_generation_pilot import audit_rows as generation_audit, rows
from audit_bounded_protection import audit_rows as protection_audit
from audit_real_structure import audit as provenance_audit
from audit_bounded_kv import audit_rows as kv_audit
import run_protection_ablation as ablation

MODES = {'zero': 0, 'one': 1, 'two': 2}


def audit_one(cell, mode, signature, trace):
    result, manifest = read(cell / 'result.json'), read(cell / 'run-manifest.json')
    snapshot = read(cell / 'generation-summary.json')
    summary = ablation.merge_protection_summary(cell / 'protection-summary')
    handoffs, service = rows(cell / 'handoffs', '*.jsonl'), rows(cell / 'owned-events', '*.jsonl')
    generation = generation_audit(handoffs, service, result)
    protected = [r for r in handoffs if r['event'] == 'worker_bound' and r['source'] == 'protection']
    transfers = [r for r in service if r['event'] == 'transfer_submitted' and r['kind'] == 'store']
    terminal_seen = {(r['request_id'], r['sequence'], r['kind']): r
                     for r in service if r['event'] == 'terminal_seen'}
    retired = {(r['request_id'], r['sequence'], r['kind']): r
               for r in service if r['event'] == 'transfer_retired'}
    actual = [b['batch_id'] for b in protected if any(
        t['request_id'] == b['wire_id'] and t['monotonic_ns'] > b['monotonic_ns']
        and {i for g in b['block_ids'] for i in g} <= {i for g in t['blocks'] for i in g}
        and (t['request_id'], t['sequence'], 'store') in terminal_seen
        and terminal_seen[(t['request_id'], t['sequence'], 'store')]['monotonic_ns'] > t['monotonic_ns']
        and (t['request_id'], t['sequence'], 'store') in retired
        and retired[(t['request_id'], t['sequence'], 'store')]['monotonic_ns'] > t['monotonic_ns']
        and retired[(t['request_id'], t['sequence'], 'store')]['succeeded'] is True
        for t in transfers)]
    pin_audit = protection_audit(handoffs, MODES[mode] * 16) if protected else None
    logs = ablation.service_error_report(cell, read(cell / 'shutdown-context.json'))
    timing = timing_audit(cell)
    preemptions = result['vllm_delta'].get('vllm:num_preemptions_total')
    is_kv = signature['gate'] == 'whole-step-service-kv-v1'
    postlude = read(cell / 'restore-postlude.json') if is_kv else None
    expected = len(trace['events']) + 1 + int(preemptions) if preemptions is not None else -1
    if is_kv:
        expected += len(postlude['requests'])
    config = WholeStepConfig(max_chunks=MODES[mode]).kv_transfer_config()
    if is_kv:
        config.update(kv_connector='WholeStepSparseProbeConnector',
                      kv_connector_module_path='scripts.whole_step_sparse_probe')
    checks = dict(result=result['passed'], tokens=token_gate(cell, trace), raw_timing=timing['passed'],
        resources=resource_gate('lazy', result), generation=generation['passed'] and generation_closed(snapshot),
        generation_count=len(snapshot['generations']) == expected,
        config=read(cell / 'config.json') == signature['configs'][mode] == config,
        source=manifest['source_sha256'] == signature['source_sha256'],
        trace=digest(trace) == digest(read(cell / 'trace.json')) == manifest['trace_sha256'] == signature['trace_sha256'] == TRACE_SHA,
        budget=manifest['kv_cache_bytes'] == signature['kv_cache_bytes'] == KV_BYTES,
        identity=manifest['mode'] == result['mode'] == mode and manifest['repeat'] == result['repeat'] == 0,
        healthy=snapshot.get('protection_poisoned') is None and not snapshot.get('fault_error') and not snapshot.get('shutdown_error'),
        receipts=summary['committed_batches'] == summary['receipts'] and summary['failed_receipts'] == 0,
        coverage=(mode == 'zero' and not protected and summary['committed_batches'] == 0) or
                 (mode != 'zero' and bool(actual) and bool(pin_audit and pin_audit['passed'])),
        bounded=summary['peak_pinned_blocks'] <= MODES[mode] * 16,
        protection_closed=summary['shutdown_closed'], h2d=result['h2d_bytes'] > 0,
        fatal_logs=not logs['fatal_errors'])
    kv = None
    if is_kv:
        post = postlude['requests']
        targets = postlude['targets']
        checks['postlude'] = (bool(targets) and len(post) == 80 + len(targets)
            and [r['label'] for r in post[:80]] == [f'evict-{i}' for i in range(80)]
            and [r['label'] for r in post[80:]] == ['restore-' + b for b in targets]
            and all(r['http_status'] == 200 and r['response']['usage']['completion_tokens'] == 1
                    and r['response']['usage']['prompt_tokens'] == len(r['payload']['prompt']) for r in post)
            and all(len(r['payload']['prompt']) == 224 for r in post[:80])
            and all(b['batch_id'] in actual for b in targets.values()))
        kv = kv_audit(handoffs, service, rows(cell / 'probe', '*.jsonl'))
        checks['strict_protected_kv'] = kv['passed']
    return dict(passed=all(checks.values()), checks=checks, requests=result['requests'],
        committed=summary['committed_batches'], receipts=summary['receipts'],
        actual_protected_store_batches=actual, peak_pinned_blocks=summary['peak_pinned_blocks'],
        fallbacks=summary['fallbacks'], generation_count=len(snapshot['generations']),
        h2d_bytes=result['h2d_bytes'], d2h_bytes=result['d2h_bytes'],
        prefill_tokens=result['vllm_delta'].get('vllm:request_prefill_kv_computed_tokens_sum'),
        preemptions=preemptions, generation_audit=generation, pin_audit=pin_audit, kv_audit=kv,
        correctness_postlude_requests=len(postlude['requests']) if is_kv else 0,
        scope=('Original 60 requests plus separate correctness postlude; strict KV proof, not timing'
               if is_kv else 'Actual protected STORE plus overall H2D; no same-chunk bitwise or performance claim'))


def audit(directory):
    directory = Path(directory)
    plan, trace = read(directory / 'plan.json'), read(directory / 'trace.json')
    signature = plan['signature']
    if (plan['stage'] not in ('coverage', 'kv') or signature['gate'] != 'whole-step-service-' + plan['stage'] + '-v1'
            or signature['hotpath_diagnostics'] is not True or digest(trace) != TRACE_SHA
            or not plan['modes'] or len(set(plan['modes'])) != len(plan['modes'])
            or any(m not in MODES for m in plan['modes'])):
        raise ValueError('Invalid whole-step coverage plan')
    with tarfile.open(directory / 'frozen-source.tar.gz') as archive:
        frozen = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                  for m in archive.getmembers() if m.isfile()}
    if frozen != signature['source_sha256']:
        raise ValueError('Frozen source mismatch')
    if plan['stage'] == 'kv':
        prior_dir = directory / 'coverage-admission'
        prior = read(prior_dir / 'plan.json')
        if prior['stage'] != 'coverage' or set(prior['modes']) != set(MODES) or not audit(prior_dir)['complete']:
            raise ValueError('Complete archived coverage prerequisite required')
        old = prior['signature']['source_sha256']
        now = signature['source_sha256']
        changed = {p for p in set(old) | set(now) if old.get(p) != now.get(p)}
        allowed = {'scripts/run_whole_step_mainline.py', 'scripts/audit_whole_step_mainline.py',
                   'scripts/whole_step_sparse_probe.py'}
        if not changed <= allowed:
            raise ValueError('Runtime changed since coverage admission')
    provenance = provenance_audit(directory / 'input/built', directory / 'input/source', directory / 'input/catalogue.json')
    if provenance != read(directory / 'provenance-audit.json') or trace != read(directory / 'input/built/trace.json'):
        raise ValueError('Anonymous input provenance mismatch')
    output = []
    for mode in plan['modes']:
        try:
            row = audit_one(directory / f'{mode}-r0', mode, signature, trace)
        except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
            row = dict(passed=False, error=repr(exc))
        output.append(dict(mode=mode, **row))
    return dict(complete=all(r['passed'] for r in output), passed_cells=sum(r['passed'] for r in output),
                rows=output, formal_eligible=False,
                scope='Diagnostic correctness only; a separate uninstrumented matched pilot is required')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.directory)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(complete=report['complete'], passed_cells=report['passed_cells'])))
    raise SystemExit(0 if report['complete'] else 1)
