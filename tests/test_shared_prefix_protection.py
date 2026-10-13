"""Real pool/manager contracts for additional references on shared prefixes."""
import pytest
pytest.importorskip('lmcache')
from test_bounded_protection import case
from test_generation_protection_bridge import combined
from cachepilot.runtime.shared_prefix import SharedPrefixProtection
from cachepilot.runtime.handoff import describe


def activate(combined):
    helper=combined[0]
    helper.__class__=SharedPrefixProtection
    helper.budget=16
    return combined


def settle(helper,bridge,store,failed=False):
    batch=bridge.inflight['old'][0]
    bridge.apply_receipts({'old'} if failed else set(),{'old':1},{'old':batch})
    helper.receipts({'old':dict(describe(store),source='protection',batch_id=batch)},
                    {'old'} if failed else set())
    return batch


@pytest.mark.parametrize('references',[1,4])
@pytest.mark.parametrize('shared_count',[1,8,16])
@pytest.mark.parametrize('failed',[False,True])
def test_receipt_releases_only_own_reference(combined,references,shared_count,failed):
    h,s,blocks,bridge,ledger,meta=activate(combined)
    shared=blocks[:shared_count]
    for _ in range(references):h.pool.touch(shared)
    before=h.pool.get_num_free_blocks()
    h.prepare(s)
    assert len(h.prepared)==1
    assert [b.ref_cnt for b in blocks[:16]]==[references+1]*shared_count+[1]*(16-shared_count)
    assert h.pool.get_num_free_blocks()==before-(16-shared_count)
    stores=bridge.commit_protection(h,1)
    bad=bridge.inflight['old'][0]+'-stale'
    with pytest.raises(ValueError,match='Stale'):bridge.apply_receipts(set(),{'old':1},{'old':bad})
    batch=settle(h,bridge,stores[0],failed)
    assert [b.ref_cnt for b in blocks[:16]]==[references]*shared_count+[0]*(16-shared_count)
    assert h.pool.get_num_free_blocks()==before
    with pytest.raises(ValueError,match='Stale'):bridge.apply_receipts(set(),{'old':1},{'old':batch})
    assert all(b.ref_cnt==references for b in shared)
    for _ in range(references):h.pool.free_blocks(shared)
    assert h.pool.get_num_free_blocks()==128


@pytest.mark.parametrize('action',['cancel','new_owner'])
@pytest.mark.parametrize('phase',['prepared','committed'])
def test_other_owner_changes_preserve_our_pin(combined,action,phase):
    h,s,blocks,bridge,*_=activate(combined)
    h.pool.touch([blocks[0]])
    h.prepare(s)
    stores=bridge.commit_protection(h,1) if phase=='committed' else None
    if action=='cancel':h.pool.free_blocks([blocks[0]])
    else:h.pool.touch([blocks[0]])
    assert blocks[0].ref_cnt==(1 if action=='cancel' else 3)
    if stores:settle(h,bridge,stores[0])
    else:assert bridge.commit_protection(h,0)==[]
    expected=0 if action=='cancel' else 2
    assert blocks[0].ref_cnt==expected
    if action=='cancel':
        # Formerly shared block was not in the original free queue. Keep the
        # native release position; do not fabricate a previous front rank.
        ranks=h.policy._free_queue_ranks(128)
        if phase=='prepared':
            assert ranks[blocks[0].block_id]>ranks[blocks[15].block_id]
        else:
            assert {ranks[b.block_id] for b in blocks[:16]}==set(range(112,128))
    else:
        assert blocks[0].block_id not in h.policy._free_queue_ranks(128)
        h.pool.free_blocks([blocks[0]]);h.pool.free_blocks([blocks[0]])
    assert h.pool.get_num_free_blocks()==128


def test_rollback_restores_only_originally_free_ranks(combined):
    h,s,blocks,bridge,ledger,*_=activate(combined)
    h.pool.touch([blocks[0]])
    before=dict(h.policy._free_queue_ranks(128));pending=dict(bridge.pending)
    for _ in range(5):
        h.prepare(s)
        assert blocks[0].block_id not in h.prepared['old'].free_ranks
        assert bridge.commit_protection(h,0)==[]
        assert dict(h.policy._free_queue_ranks(128))==before
        assert bridge.pending==pending and not ledger.derived_from
        assert blocks[0].ref_cnt==1
    h.pool.free_blocks([blocks[0]])


def test_shared_hash_mismatch_still_rejected(combined):
    h,s,blocks,*_=activate(combined)
    h.pool.touch([blocks[0]])
    h.policy._pending['old'][0].block_hashes[blocks[0].block_id]=b'wrong'
    h.prepare(s)
    assert not h.prepared and blocks[0].ref_cnt==1


def test_post_register_fault_never_rolls_back_shared_pin(combined,monkeypatch):
    h,s,blocks,bridge,ledger,meta=activate(combined)
    h.pool.touch([blocks[0]]);h.prepare(s)
    original=bridge.manager._requests.register_batch
    def broken(*args):
        original(*args)
        raise RuntimeError('after native registration')
    monkeypatch.setattr(bridge.manager._requests,'register_batch',broken)
    with pytest.raises(RuntimeError,match='registration'):bridge.commit_protection(h,1)
    with pytest.raises(RuntimeError,match='reconstruction'):h.rollback('exception')
    assert blocks[0].ref_cnt==2 and all(b.ref_cnt==1 for b in blocks[1:16])
    assert bridge.poisoned and ledger.submit_ends(lambda _:pytest.fail('unsafe END'))==[]


def test_model_service_bind_is_not_admitted(combined):
    h,*_=activate(combined)
    with pytest.raises(RuntimeError,match='independent service admission'):h.bind(None)


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('ending',['receipt','rollback','partial_fault'])
def test_two_protected_generations_share_blocks_and_retire_independently(combined,reverse,ending,monkeypatch):
    from dataclasses import replace
    from cachepilot.integration.lmcache.protocol import wire_id,SEQUENCE_CONFIG
    h,s,blocks,bridge,ledger,meta=activate(combined)
    h.budget=32
    # Limit each candidate to a single chunk so the budget admits both.
    bridge.on_request_reset('old')
    # Use fresh raw IDs/generations; the original reset remains a terminal old generation.
    for index,rid in enumerate(['left','right'],start=2):
        candidate=replace(meta,request_id=rid,op=replace(meta.op,end=256,block_ids=[meta.op.block_ids[0][:16]]),
            request_configs=dict(meta.request_configs,**{SEQUENCE_CONFIG:index}))
        ledger.register(rid,wire_id(candidate));bridge.on_request_arrived(rid)
        bridge.add_store_candidate(candidate);bridge.on_request_finished(rid)
    h.pool.touch([blocks[0]])
    before=dict(h.policy._free_queue_ranks(128))
    h.prepare(s)
    assert len(h.prepared)==2
    assert blocks[0].ref_cnt==3 and all(b.ref_cnt==2 for b in blocks[1:16])
    if ending=='rollback':
        assert bridge.commit_protection(h,0)==[]
        assert blocks[0].ref_cnt==1 and all(b.ref_cnt==0 for b in blocks[1:16])
        assert dict(h.policy._free_queue_ranks(128))==before
        return
    if ending=='partial_fault':
        original=bridge.manager._requests.register_batch
        calls=[]
        def fault(*args):
            calls.append(args[0])
            if len(calls)==2:
                if reverse:original(*args)
                raise RuntimeError('second shared registration')
            return original(*args)
        monkeypatch.setattr(bridge.manager._requests,'register_batch',fault)
        with pytest.raises(RuntimeError,match='second shared'):bridge.commit_protection(h,1)
        with pytest.raises(RuntimeError,match='reconstruction'):h.rollback('partial failure')
        assert blocks[0].ref_cnt==3 and all(b.ref_cnt==2 for b in blocks[1:16])
        assert ledger.submit_ends(lambda _:pytest.fail('unsafe END'))==[]
        return
    stores=bridge.commit_protection(h,1)
    for index,store in enumerate(reversed(stores) if reverse else stores):
        rid=store.request_id;batch=bridge.inflight[rid][0]
        bridge.apply_receipts(set(),{rid:1},{rid:batch})
        h.receipts({rid:dict(describe(store),source='protection',batch_id=batch)},set())
        assert blocks[0].ref_cnt==2-index
        assert all(b.ref_cnt==1-index for b in blocks[1:16])
    h.pool.free_blocks([blocks[0]])
    assert h.pool.get_num_free_blocks()==128


@pytest.mark.parametrize('failed',[False,True])
def test_old_generation_receipt_preserves_shared_successor(combined,failed):
    from dataclasses import replace
    from cachepilot.integration.lmcache.protocol import wire_id,SEQUENCE_CONFIG
    h,s,blocks,bridge,ledger,meta=activate(combined)
    h.pool.touch([blocks[0]]);h.prepare(s)
    stores=bridge.commit_protection(h,1)
    bridge.on_request_reset('old')
    new=replace(meta,request_configs={**meta.request_configs,SEQUENCE_CONFIG:2})
    ledger.register('old',wire_id(new));bridge.on_request_arrived('old');bridge.add_store_candidate(new)
    before=dict(bridge.pending)
    settle(h,bridge,stores[0],failed)
    assert bridge.pending==before and blocks[0].ref_cnt==1
    assert 'old' not in h.policy._broken_prefixes
    ledger.seal(wire_id(meta));assert ledger.ready(wire_id(meta))
    assert all(ledger.generations[wire_id(new)].operations[t]=='pending' for t in before.values())


def test_preflight_rejection_rolls_back_only_shared_own_ref(combined):
    from dataclasses import replace
    h,s,blocks,bridge,*_=activate(combined)
    h.pool.touch([blocks[0]]);h.prepare(s)
    prepared=h.prepared['old'];prepared.metadata=replace(prepared.metadata,op=replace(prepared.metadata.op,end=240))
    before=dict(bridge.pending)
    with pytest.raises(ValueError):bridge.commit_protection(h,1)
    assert bridge.pending==before and not bridge.inflight
    h.rollback('preflight')
    assert blocks[0].ref_cnt==1 and all(b.ref_cnt==0 for b in blocks[1:16])
