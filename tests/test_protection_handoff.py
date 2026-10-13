import pytest
from cachepilot.runtime.handoff import HandoffLedger, ProtectionConfig


@pytest.mark.parametrize("extra", [{"cachepilot.protection.enabled": 1},
    {"cachepilot.protection.max_chunks": True}, {"cachepilot.protection.max_chunks": -1},
    {"cachepilot.protection.max_chunks": 3}, {"cachepilot.protection.max_chunks": 0.0}])
def test_unsupported_settings_fail_closed(extra):
    with pytest.raises(ValueError):
        ProtectionConfig.parse(extra)


def test_zero_and_disabled_are_separate_modes():
    assert ProtectionConfig.parse({}) == ProtectionConfig(False, 0)
    assert ProtectionConfig.parse({"cachepilot.protection.enabled": True}) == ProtectionConfig(True, 0)


def test_receipt_keeps_original_generation_and_failure():
    rows = []
    ledger = HandoffLedger(lambda phase, **kw: rows.append((phase, kw)))
    first = dict(request_id="reused", batch_id="generation1:batch1")
    ledger.accept(first, "built")
    with pytest.raises(RuntimeError):
        ledger.accept(dict(first, batch_id="generation2:batch1"), "built")
    with pytest.raises(ValueError):
        ledger.verify({"reused": 1}, set(), {"reused": "generation2:batch1"})
    assert ledger.pending["reused"] == first
    batches = ledger.verify({"reused": 1}, {"reused"}, {"reused": first["batch_id"]})
    ledger.retire(batches, {"reused"}, "receipt")
    assert not ledger.pending and rows[-1][1]["failed"] is True
    ledger.accept(dict(first, batch_id="generation2:batch1"), "built")
    with pytest.raises(ValueError):
        ledger.verify({"reused": 1}, set(), {"reused": first["batch_id"]})


@pytest.mark.parametrize("counts,failed,ids", [({"r": 2}, set(), {"r": "b"}),
    ({"r": True}, set(), {"r": "b"}), ({}, {"r"}, {}),
    ({"other": 1}, set(), {"other": "b"}), ({"r": 1}, set(), {})])
def test_bad_receipts_do_not_retire(counts, failed, ids):
    ledger = HandoffLedger(lambda *a, **kw: None)
    ledger.accept(dict(request_id="r", batch_id="b"), "built")
    with pytest.raises(ValueError):
        ledger.verify(counts, failed, ids)
    assert "r" in ledger.pending
