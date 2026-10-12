"""Joint generation and shared-reference GPU audit, recomputed from raw events."""
from pathlib import Path
from scripts.audit_generation_protection_smoke import audit_cell
from scripts.audit_generation_pilot import rows
from scripts.audit_protection_shared_cancel import audit as cancel_audit
from scripts.audit_protection_shared_preemption import audit as preemption_audit


def audit(directory, preemption=False):
    directory = Path(directory)
    normal = audit_cell(directory)
    assert normal['passed']
    events = rows(directory / 'events', 'handoff-*.jsonl')
    if preemption:
        shared = preemption_audit(directory)
    else:
        shared = cancel_audit(directory)
    targets = [r for r in events if r['event'] == 'shared_target']
    assert len(targets) == 1
    target = targets[0]
    assert any(r['batch_id'] == target['batch_id'] for r in normal['sources']['strict_sources'])
    assert sum(r['event'] == 'generation_end_submitted' and r['wire_id'] == target['wire_id'] for r in events) == 1
    return dict(passed=True, normal=normal, shared=shared,
        kind='shared-preemption' if preemption else 'shared-cancel')
