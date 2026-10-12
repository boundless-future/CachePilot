"""Recompute new-stack normal GPU evidence; saved pass flags are insufficient."""
import argparse
import json
from pathlib import Path
from scripts.audit_generation_pilot import audit_rows, rows
from scripts.audit_owned_service import audit as ownership_audit
from scripts.audit_protection_handoff import audit as handoff_audit
from scripts.audit_bounded_protection import audit_rows as protection_audit
from scripts.audit_bounded_kv import audit as source_audit
from scripts.analyze_kv_probe import summarize_probe


def audit_cell(cell):
    cell = Path(cell)
    read = lambda name: json.loads((cell / name).read_text(encoding='utf-8'))
    result = read('result.json')
    handoffs = rows(cell / 'events', 'handoff-*.jsonl')
    service = rows(cell / 'events', 'owned-service-*.jsonl')
    generation = audit_rows(handoffs, service, dict(
        status_before_engine_stop=result.get('running_final', {}),
        status_after_engine_stop=result.get('after_engine_stop', {})))
    ownership = ownership_audit(cell)
    handoff = handoff_audit(cell / 'events', allow_protection=result['mode'] != 'zero')
    kv = summarize_probe(rows(cell / 'probe', 'kv-*.jsonl'))
    assert kv['covered_and_equal']
    snapshot = read('generation.json')
    assert snapshot and snapshot.get('protection_poisoned') is None
    assert not snapshot.get('shutdown_error')
    assert all(not snapshot[k] for k in ('pending_store', 'inflight_store', 'retrieves', 'pending_rpc', 'deferred_ends'))
    assert all(g['sealed'] and g['end_state'] == 'submitted' and g['error'] is None
        and all(op['state'] in ('terminal', 'dropped') for op in g['operations'])
        for g in snapshot['generations'])
    extra = {}
    if result['mode'] != 'zero':
        extra['protection'] = protection_audit(handoffs, budget=16 if result['mode'] == 'one' else 32)
        extra['sources'] = source_audit(cell)
    return dict(passed=bool(result['passed'] and generation['passed']),
        mode=result['mode'], generation=generation, ownership=ownership,
        handoff=handoff, kv=kv, **extra)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('cell', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit_cell(args.cell)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=result['passed'], mode=result['mode'])))
    raise SystemExit(0 if result['passed'] else 1)
