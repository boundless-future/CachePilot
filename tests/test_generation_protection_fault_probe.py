"""Fault placement is after actual native registration and before handoff."""
import pytest
pytest.importorskip('lmcache')
from test_bounded_protection import case
from test_generation_protection_connector import wired, step
from scripts.generation_protection_fault_probe import GenerationProtectionFaultProbeConnector


def test_fault_records_actual_pin_ownership_before_throw(wired, monkeypatch, tmp_path):
    import json
    obj, helper, scheduler, blocks, transport, meta = wired
    obj.__class__ = GenerationProtectionFaultProbeConnector
    rows = []
    obj._handoff_record = lambda event, **fields: rows.append(dict(event=event, **fields))
    monkeypatch.setenv('CACHEPILOT_GENERATION_FAULT', 'after_commit')
    target = tmp_path / 'fault.json'
    monkeypatch.setenv('CACHEPILOT_GENERATION_SUMMARY_PATH', str(target))
    helper.prepare(scheduler)
    with pytest.raises(RuntimeError, match='CACHEPILOT_INJECTED_AFTER_PROTECTION_COMMIT'):
        obj.build_connector_meta(step())
    event, = [r for r in rows if r['event'] == 'generation_fault_injected']
    assert len(event['targets']) == 1
    assert event['targets'][0]['native_inflight']
    assert event['targets'][0]['ref_counts'] == [1] * 16
    assert not obj._handoffs.pending and not transport.calls
    assert helper.manager.poisoned and sum(b.ref_cnt for b in blocks) == 16
    snapshot = json.loads(target.read_text())
    assert snapshot['fault_phase'] == 'build_connector_meta'
    assert snapshot['protected_inflight_blocks'] == 16 and snapshot['inflight_store'] == 1
