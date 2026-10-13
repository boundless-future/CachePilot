"""Independent evidence joins for already-shared protected prefixes."""
import json
import hashlib
from pathlib import Path
from scripts.audit_generation_pilot import rows, audit_rows as generation_audit
from scripts.audit_bounded_protection import audit_rows as bounded_audit
from scripts.audit_bounded_kv import audit_rows as source_audit
from scripts.audit_owned_service import audit as ownership_audit
from scripts.analyze_kv_probe import summarize_probe


def audit_postlude(data, handoffs):
    targets = {r['prefix_sha256']:r for r in handoffs if r['event'] == 'scheduler_built'
               and r['source'] == 'protection' and r['batch_id'] in {
                   v['batch_id'] for v in data['targets'].values()}}
    assert data['targets'] == targets and targets
    requests = data['requests']
    assert len(requests) == 320 + len(targets)
    assert [r['label'] for r in requests[:160]] == [f'evict-{i}' for i in range(160)]
    assert [r['label'] for r in requests[-160:]] == [f'final-{i}' for i in range(160)]
    for r in requests:
        payload, response = r['payload'], r['response']
        assert payload['max_tokens'] == response['usage']['completion_tokens'] == 1
        assert response['usage']['prompt_tokens'] == len(payload['prompt'])
        assert payload['temperature'] == 0 and payload['seed'] == 42 and payload['ignore_eos'] is True
        if r['label'].startswith(('evict-', 'final-')):
            assert len(payload['prompt']) == 224
    restores = requests[160:-160]
    assert {r['label'] for r in restores} == {'restore-' + k for k in targets}
    for r in restores:
        ids = r['payload']['prompt']
        assert len(ids) == 257
        key = hashlib.sha256(json.dumps(['', ids[:256]], separators=(',', ':')).encode()).hexdigest()
        assert r['label'] == 'restore-' + key and key in targets
    return dict(passed=True, requests=len(requests), restore_attempts=len(restores),
        eviction_requests=320, performance=False)


def shared_evidence(handoffs):
    prepared = [r for r in handoffs if r['event'] == 'protection_prepared']
    assert prepared
    for r in prepared:
        assert len(r['refs_before']) == len(r['refs_after']) == r['acquired_pins']
        assert all(a >= 0 and b == a + 1 for a, b in zip(r['refs_before'], r['refs_after']))
        assert r['shared_blocks_before'] == sum(n > 0 for n in r['refs_before'])
    pending, committed, commits = {}, {}, []
    for _, r in sorted(enumerate(handoffs), key=lambda item:item[1].get('monotonic_ns', item[0])):
        event, wire = r['event'], r.get('wire_id')
        if event == 'protection_prepared':
            assert wire not in pending
            pending[wire] = r
        elif event == 'protection_rollback':
            assert wire in pending
            pending.pop(wire)
        elif event == 'protection_metadata_committed':
            assert wire in pending and wire not in committed
            committed[wire] = pending.pop(wire)
        elif event == 'scheduler_built' and r['source'] == 'protection':
            assert wire in committed
            original = committed.pop(wire)
            if original['shared_blocks_before'] > 0:
                commits.append(r)
    assert not pending and not committed
    return dict(shared_prepares=sum(r['shared_blocks_before'] > 0 for r in prepared),
        shared_committed_batches=[r['batch_id'] for r in commits],
        zero_step_rollbacks=sum(r['event'] == 'protection_rollback' and
                               r.get('reason') == 'zero_token_step' for r in handoffs))


def coverage_audit(directory):
    directory = Path(directory)
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    cell = directory / 'one'
    r, plan = read(cell / 'result.json'), read(directory / 'plan.json')
    h, s = rows(cell / 'events', 'handoff-*.jsonl'), rows(cell / 'events', 'owned-service-*.jsonl')
    g = generation_audit(h, s, r)
    shared = shared_evidence(h)
    bounded = bounded_audit(h, 16)
    from scripts.run_generation_matrix import token_gate, generation_closed
    from scripts.run_generation_strategy_pilot import digest, strategy_config, CASES, KV_BYTES
    from scripts.audit_generation_strategy_validation import VALIDATION_SHA
    from scripts.run_reference_baselines import resource_gate
    original = read(Path(__file__).resolve().parents[1] / 'configs/protection-zero.json')
    expected = strategy_config(original, CASES['selected-one'])
    expected.update(kv_connector='SharedPrefixConnector', kv_connector_module_path='scripts.shared_prefix_connector')
    expected['kv_connector_extra_config']['cachepilot.shared_prefix_protection'] = True
    manifest = read(cell / 'run-manifest.json')
    checks = dict(result=r['passed'], generation=g['passed'], shared=bool(shared['shared_committed_batches']),
        tokens=token_gate(cell, read(cell / 'trace.json')), resources=resource_gate('lazy', r),
        config=read(cell / 'config.json') == expected, budget=manifest['kv_cache_bytes'] == KV_BYTES,
        snapshot=generation_closed(read(cell / 'generation.json')),
        source=manifest['source_sha256'] == plan['signature']['source_sha256'],
        trace=digest(read(cell / 'trace.json')) == plan['trace_sha256'] == manifest['trace_sha256'] == VALIDATION_SHA)
    return dict(passed=all(checks.values()), checks=checks, shared=shared, bounded=bounded,
        generation=g, performance=False, kv_bitwise=False,
        scope='Historical input coverage and resource diagnostic without synchronizing KV probes')


def audit(directory):
    directory = Path(directory)
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    cell = directory / 'one'
    result, plan = read(cell / 'result.json'), read(directory / 'plan.json')
    h, s, p = rows(cell / 'events', 'handoff-*.jsonl'), rows(cell / 'events', 'owned-service-*.jsonl'), rows(cell / 'probe', 'kv-*.jsonl')
    generation = generation_audit(h, s, result)
    bounded, ownership, sources = bounded_audit(h, 16), ownership_audit(cell), source_audit(h, s, p)
    shared, kv = shared_evidence(h), summarize_probe(p)
    shared_sources = [r for r in sources['strict_sources'] if r['batch_id'] in shared['shared_committed_batches']]
    manifest = read(cell / 'run-manifest.json')
    from scripts.run_generation_strategy_pilot import digest
    from scripts.audit_generation_strategy_validation import VALIDATION_SHA
    from scripts.run_generation_strategy_pilot import strategy_config, CASES, KV_BYTES
    from scripts.run_generation_matrix import token_gate, generation_closed
    from scripts.run_reference_baselines import resource_gate
    from scripts.run_protection_ablation import service_error_report
    original = read(Path(__file__).resolve().parents[1] / 'configs/protection-zero.json')
    expected = strategy_config(original, CASES['selected-one'])
    expected.update(kv_connector='SharedPrefixProbeConnector', kv_connector_module_path='scripts.shared_prefix_probe')
    expected['kv_connector_extra_config']['cachepilot.shared_prefix_protection'] = True
    if plan.get('windows'):
        expected.update(kv_connector='SharedPrefixWindowProbeConnector',
                        kv_connector_module_path='scripts.shared_prefix_window_probe')
    if plan.get('sparse'):
        expected.update(kv_connector='SharedPrefixSparseProbeConnector',
                        kv_connector_module_path='scripts.shared_prefix_sparse_probe')
    snapshot = read(cell / 'generation.json')
    postlude = audit_postlude(read(cell / 'restore-postlude.json'), h) if plan.get('restore_postlude') else None
    logs = service_error_report(cell, read(cell / 'shutdown-context.json'))
    checks = dict(result=result['passed'], generation=generation['passed'], kv=kv['covered_and_equal'],
        config=read(cell / 'config.json') == expected, budget=manifest['kv_cache_bytes'] == KV_BYTES,
        tokens=token_gate(cell, read(cell / 'trace.json')), resources=resource_gate('lazy', result),
        generation_snapshot=generation_closed(snapshot), fatal_logs=not logs['fatal_errors'],
        shared_source=bool(shared_sources), source=manifest['source_sha256'] == plan['signature']['source_sha256'],
        trace=digest(read(cell / 'trace.json')) == manifest['trace_sha256'] == plan['trace_sha256'] == VALIDATION_SHA,
        healthy=snapshot.get('protection_poisoned') is None and not snapshot.get('shutdown_error'),
        closed=all(not snapshot[k] for k in ('pending_store', 'inflight_store', 'retrieves', 'pending_rpc', 'deferred_ends')))
    return dict(passed=all(checks.values()), checks=checks, generation=generation,
        bounded=bounded, ownership=ownership, sources=sources, shared=shared, kv=kv,
        shared_strict_sources=shared_sources, postlude=postlude, logs=logs, performance=False)
