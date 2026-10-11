"""Real manager/policy/BlockPool contracts, no model inference."""
from types import SimpleNamespace as NS
import pytest

pytest.importorskip('lmcache')
from test_bounded_protection import case
from scripts.idle_finalize import finalize_idle_pending
from scripts.owned_service_protocol import wire_id, GENERATION_CONFIG


def setup(helper, scheduler):
    scheduler.requests = {}
    scheduler.running = scheduler.waiting = scheduler.skipped_waiting = []
    scheduler.deferred_frees = {}
    meta = helper.policy._pending['old'][0].store_metadata
    client = NS(identities={'old': wire_id(meta)})
    ended = []
    def end(rid):
        ended.append(client.identities.pop(rid))
    c = NS(_bounded=helper, _handoffs=NS(pending={}),
        _lazy_offload_manager=helper.manager, _handoff_record=helper.record,
        scheduler_adapter=NS(req_clients={'url':client}, end_session=end))
    return c, ended, client, meta


def pool_state(pool):
    return (pool.get_num_free_blocks(),
            [(b.ref_cnt,b.block_hash) for b in pool.blocks],
            [b.block_id for b in pool.free_block_queue.get_all_free_blocks()])


def test_idle_drop_keeps_shared_gpu_cache_and_policy_accounting(case):
    helper,s,blocks,rows=case
    c,ended,client,meta=setup(helper,s)
    helper.pool.touch(blocks)  # A separate reference remains outside this policy.
    before=pool_state(helper.pool)
    assert finalize_idle_pending(c,s)==1
    assert pool_state(helper.pool)==before
    assert helper.policy._counters.dropped_on_request_drop==1
    assert not helper.policy._pending and not helper.manager._requests.is_finished('old')
    assert ended==[wire_id(meta)]
    helper.pool.free_blocks(blocks)


def test_real_submitted_protection_batch_waits_for_original_receipt(case):
    helper,s,blocks,rows=case
    helper.prepare(s); stores=helper.commit(256)
    assert stores and helper.manager._requests.has_in_flight('old')
    # Add an independent finished pending request to prove global deferral.
    from dataclasses import replace
    meta=replace(stores[0],request_id='other')
    helper.manager.on_request_arrived('other')
    helper.policy.add(meta,{b.block_id:b.block_hash for b in blocks})
    helper.manager.on_request_finished('other')
    s.requests={}; s.running=s.waiting=s.skipped_waiting=[]; s.deferred_frees={}
    ended=[]
    c=NS(_bounded=helper,_handoffs=NS(pending={}),_lazy_offload_manager=helper.manager,
        _handoff_record=helper.record,scheduler_adapter=NS(req_clients={
            'url':NS(identities={'other':wire_id(meta)})},end_session=ended.append))
    before=pool_state(helper.pool)
    assert finalize_idle_pending(c,s)==0 and pool_state(helper.pool)==before
    assert helper.policy.has_pending_request('other') and not ended
    helper.manager.on_store_results(set(),{'old':1})
    assert all(b.ref_cnt==0 for b in blocks)


def test_new_generation_after_finalization_stays_independent(case):
    helper,s,blocks,rows=case
    c,ended,client,meta=setup(helper,s)
    old_identity=wire_id(meta)
    assert finalize_idle_pending(c,s)==1
    from dataclasses import replace
    new=replace(meta,request_configs=dict(meta.request_configs,**{GENERATION_CONFIG:'b'*32}))
    helper.manager.on_request_arrived('old')
    helper.manager.add_store_candidate(new)
    client.identities['old']=wire_id(new)
    # New generation is active, so it must not inherit old finished state.
    with pytest.raises(RuntimeError,match='not a finished'):
        finalize_idle_pending(c,s)
    assert ended==[old_identity] and helper.policy.has_pending_request('old')
    helper.manager.on_request_finished('old')
    assert finalize_idle_pending(c,s)==1
    assert ended==[old_identity,wire_id(new)]


@pytest.mark.parametrize('identity_state', ['ended', 'replacement'])
def test_stale_pending_identity_preserves_real_manager_and_does_not_end_new_owner(case, identity_state):
    """Characterize the long-prefill guard, without claiming its live event cause."""
    from dataclasses import replace
    from scripts.owned_service_protocol import OwnedRequestClient
    from test_owned_service_protocol import Client
    helper,s,blocks,rows=case
    c,ended,client,meta=setup(helper,s)
    transport=Client()
    proxy=OwnedRequestClient(transport,7)
    proxy.lookup(meta)
    if identity_state=='ended':
        proxy.end_session('old')
    else:
        replacement=replace(meta,request_configs=dict(meta.request_configs,
            **{GENERATION_CONFIG:'b'*32}))
        proxy.lookup(replacement)
    c.scheduler_adapter.req_clients={'url':proxy}
    c.scheduler_adapter.end_session=proxy.end_session
    before_pool=pool_state(helper.pool)
    before_rpc=list(transport.calls)
    original_pending=helper.policy._pending['old']
    with pytest.raises(RuntimeError,match='current wire identity'):
        finalize_idle_pending(c,s)
    assert pool_state(helper.pool)==before_pool
    assert helper.policy._pending['old'] is original_pending
    assert helper.manager._requests.is_finished('old')
    assert transport.calls==before_rpc and not ended
    assert not any(r['event']=='idle_pending_dropped' for r in rows)
