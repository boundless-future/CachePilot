"""The optional eligibility gate skips work without weakening pin admission."""
from dataclasses import replace
import pytest

pytest.importorskip('lmcache')
pytest.importorskip('vllm')
from cachepilot_test_support.pool import case, pool_state
from cachepilot_test_support.generation import combined
from cachepilot.runtime.whole_step_protection import WholeStepProtection


@pytest.mark.parametrize('state', ['empty', 'active', 'budget', 'suffix', 'salt'])
def test_ineligible_state_skips_reserve_and_preserves_pool(combined, monkeypatch, state):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.__class__ = WholeStepProtection
    helper.skip_ineligible = True
    scheduler._inflight_prefills = []
    if state == 'empty':
        helper.policy._pending.clear()
    elif state == 'active':
        scheduler.requests['old'] = scheduler.requests['new']
    elif state == 'budget':
        helper.budget = 0
    else:
        pending = helper.policy._pending['old'][0]
        if state == 'suffix':
            changed = replace(meta, op=replace(meta.op, start=256, block_ids=[meta.op.block_ids[0][16:]]))
        else:
            changed = replace(meta, cache_salt='unsupported')
        helper.policy._pending['old'][0] = replace(pending, store_metadata=changed)
    before_pool = pool_state(helper.pool)
    before_pending = {rid: list(ops) for rid, ops in helper.policy._pending.items()}
    before_ledger = dict(bridge.pending)

    def unexpected(_):
        raise AssertionError('Ineligible state reached the envelope scan')

    monkeypatch.setattr(helper.admission_policy, 'reserve', unexpected)
    helper.prepare(scheduler)
    assert pool_state(helper.pool) == before_pool
    assert helper.policy._pending == before_pending and bridge.pending == before_ledger
    assert not helper.prepared and not helper.inflight


@pytest.mark.parametrize('fault', ['poisoned', 'unsettled'])
def test_eligibility_gate_preserves_failure_guards(combined, fault):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.__class__ = WholeStepProtection
    helper.skip_ineligible = True
    helper.policy._pending.clear()
    if fault == 'poisoned':
        bridge.poison(RuntimeError('ambiguous commit'))
    else:
        helper.prepared['old'] = object()
    with pytest.raises(RuntimeError, match='reconstruction|Unsettled'):
        helper.prepare(scheduler)
