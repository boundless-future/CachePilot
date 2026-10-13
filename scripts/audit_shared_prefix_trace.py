"""Independent evidence joins for already-shared protected prefixes."""
import json
from pathlib import Path
from scripts.audit_generation_pilot import rows, audit_rows as generation_audit
from scripts.audit_bounded_protection import audit_rows as bounded_audit
from scripts.audit_bounded_kv import audit_rows as source_audit
from scripts.audit_owned_service import audit as ownership_audit
from scripts.analyze_kv_probe import summarize_probe


def shared_evidence(handoffs):
    prepared = [r for r in handoffs if r['event'] == 'protection_prepared']
    assert prepared
    for r in prepared:
        assert len(r['refs_before']) == len(r['refs_after']) == r['acquired_pins']
        assert all(a >= 0 and b == a + 1 for a, b in zip(r['refs_before'], r['refs_after']))
        assert r['shared_blocks_before'] == sum(n > 0 for n in r['refs_before'])
    shared = {r['wire_id'] for r in prepared if r['shared_blocks_before'] > 0}
    commits = [r for r in handoffs if r['event'] == 'scheduler_built'
               and r['source'] == 'protection' and r['wire_id'] in shared]
    return dict(shared_prepares=sum(r['shared_blocks_before'] > 0 for r in prepared),
        shared_committed_batches=[r['batch_id'] for r in commits],
        zero_step_rollbacks=sum(r['event'] == 'protection_rollback' and
                               r.get('reason') == 'zero_token_step' for r in handoffs))


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
    snapshot = read(cell / 'generation.json')
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
        shared_strict_sources=shared_sources, logs=logs, performance=False)
