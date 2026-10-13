"""Real manager/generation handoff for the CPU-only envelope candidate."""
import pytest
pytest.importorskip('lmcache')
from cachepilot_test_support.pool import case
from cachepilot_test_support.generation import combined
from cachepilot.runtime.whole_step_protection import WholeStepProtection
from cachepilot.runtime.handoff import describe


def activate(combined):
    h,s,*_=combined
    h.__class__=WholeStepProtection
    h.budget=16
    s._inflight_prefills=[]
    return combined


@pytest.mark.parametrize('failed',[False,True])
def test_tighter_envelope_preserves_original_commit_and_receipt(combined,failed):
    h,s,blocks,bridge,ledger,meta=activate(combined)
    # 49 live footprint blocks: old reserve114 rejects with free128; the
    # candidate reserve65 leaves enough capacity for a complete16-block pin.
    s.requests['new'].num_tokens_with_spec=768
    assert h.admission_policy.reserve(s)==65
    h.prepare(s)
    assert len(h.prepared)==1 and all(b.ref_cnt==1 for b in blocks[:16])
    stores=bridge.commit_protection(h,1)
    batch=bridge.inflight['old'][0]
    bridge.apply_receipts({'old'} if failed else set(),{'old':1},{'old':batch})
    h.receipts({'old':dict(describe(stores[0]),source='protection',batch_id=batch)},
               {'old'} if failed else set())
    assert not h.inflight and h.pool.get_num_free_blocks()==128
    assert all(b.ref_cnt==0 for b in blocks)


def test_zero_compute_rollback_preserves_pending_generation(combined):
    h,s,blocks,bridge,ledger,meta=activate(combined)
    before=dict(h.policy._free_queue_ranks(128));pending=dict(bridge.pending)
    h.prepare(s)
    assert bridge.commit_protection(h,0)==[]
    assert before==dict(h.policy._free_queue_ranks(128)) and pending==bridge.pending
    assert not h.prepared and not h.inflight and not ledger.derived_from


def test_service_binding_is_deliberately_rejected(combined):
    h,*_=activate(combined)
    with pytest.raises(RuntimeError,match='service admission'):h.bind(object())


def test_registered_then_raised_commit_keeps_native_owned_pins(combined,monkeypatch):
    h,s,blocks,bridge,ledger,meta=activate(combined)
    h.prepare(s)
    register=bridge.manager._requests.register_batch
    def fault(*args):
        register(*args)
        raise RuntimeError('after native registration')
    monkeypatch.setattr(bridge.manager._requests,'register_batch',fault)
    with pytest.raises(RuntimeError,match='after native registration'):
        bridge.commit_protection(h,1)
    assert bridge.manager._requests.has_in_flight('old')
    assert all(b.ref_cnt==1 for b in blocks[:16])
    with pytest.raises(RuntimeError,match='reconstruction'):h.rollback('exception')
    assert all(b.ref_cnt==1 for b in blocks[:16])


def test_changed_remote_membership_rejects_before_any_pin(combined):
    from types import SimpleNamespace as NS
    h,s,blocks,bridge,ledger,meta=activate(combined)
    before=dict(h.policy._free_queue_ranks(128))
    s._inflight_prefills=[NS(request_id='unknown')]
    with pytest.raises(ValueError,match='membership'):h.prepare(s)
    assert not h.prepared and not h.inflight
    assert before==dict(h.policy._free_queue_ranks(128))
    assert all(b.ref_cnt==0 for b in blocks)
