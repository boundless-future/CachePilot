"""Independent fault exit audit; no use of saved validation pass flags."""
import json
from pathlib import Path
from scripts.audit_generation_pilot import rows


def audit(directory):
    cell = Path(directory) / 'after-commit'
    events = rows(cell / 'events', 'handoff-*.jsonl')
    injections = [r for r in events if r['event'] == 'generation_fault_injected']
    assert len(injections) == 1
    injected = injections[0]
    targets = injected['targets']
    assert targets
    wires = {t['wire_id'] for t in targets}
    assert all(t['native_inflight'] and t['ref_counts'] and all(n > 0 for n in t['ref_counts']) for t in targets)
    assert not any(r['event'] in ('worker_bound', 'generation_end_submitted', 'protection_rollback')
        and r.get('wire_id') in wires and r['monotonic_ns'] > injected['monotonic_ns'] for r in events)
    stopped = json.loads((cell / 'before-harness-stop.json').read_text(encoding='utf-8'))
    assert stopped['before_harness_signal'] and stopped['api_returncode'] is not None
    assert stopped['engine_states'] and all(s in ('absent', 'Z') for s in stopped['engine_states'].values())
    snapshot_path = cell / 'generation.json'
    if not snapshot_path.exists():
        return dict(passed=False, reason='failure snapshot absent', autonomous_exit=True)
    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
    assert snapshot['protection_poisoned'] and snapshot['shutdown_complete'] is False
    assert snapshot['protected_inflight_blocks'] == sum(len(t['ref_counts']) for t in targets)
    assert snapshot['inflight_store'] >= len(targets)
    gs = {g['wire_id']: g for g in snapshot['generations']}
    assert all(gs[w]['end_state'] != 'submitted' and gs[w]['error']
        and any(op['state'] not in ('terminal', 'dropped') for op in gs[w]['operations']) for w in wires)
    fault_snapshots = [r for r in events if r['event'] == 'generation_fault_snapshot']
    assert fault_snapshots and fault_snapshots[-1]['monotonic_ns'] > injected['monotonic_ns']
    assert stopped['snapshot_exists']
    assert 'CACHEPILOT_INJECTED_AFTER_PROTECTION_COMMIT' in (cell / 'vllm.log').read_text(encoding='utf-8')
    return dict(passed=True, autonomous_exit=True, fault_phase=snapshot['fault_phase'],
        protected_blocks=snapshot['protected_inflight_blocks'], unresolved_wires=sorted(wires),
        scope='host exception before worker handoff; no assertion of normal drain or automatic recovery')
