from types import SimpleNamespace as NS
from dataclasses import replace
import pytest

pytest.importorskip('lmcache')
from test_bounded_protection import case
from scripts.generation_protection_bridge import (
    GenerationProtectionBridge, ProtectionGenerationLedger, GenerationBoundedProtection)
from scripts.generation_manager_bridge import operation_key
from scripts.owned_service_protocol import wire_id, SEQUENCE_CONFIG
from scripts.protection_handoff import describe
from scripts.bounded_protection import install_protection_scheduler


@pytest.fixture
def combined(case):
    helper, scheduler, blocks, rows = case
    candidate = object.__new__(GenerationBoundedProtection)
    candidate.__dict__.update(helper.__dict__)
    helper = candidate
    meta = helper.policy._pending['old'][0].store_metadata
    helper.manager.on_request_reset('old')
    ledger = ProtectionGenerationLedger()
    bridge = GenerationProtectionBridge(helper.manager, ledger)
    helper.manager = bridge
    ledger.register('old', wire_id(meta))
    bridge.on_request_arrived('old')
    bridge.add_store_candidate(meta)
    bridge.on_request_finished('old')
    return helper, scheduler, blocks, bridge, ledger, meta


@pytest.mark.parametrize('chunks', [1, 2])
@pytest.mark.parametrize('failed', [False, True])
def test_original_receipt_settles_prefix_then_suffix(combined, chunks, failed):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.budget = chunks * 16
    parent = bridge.pending[operation_key(meta)]
    helper.prepare(scheduler)
    ledger.seal(wire_id(meta))  # Splitting existing work is not a new producer.
    stores = bridge.commit_protection(helper, 1)
    batch = bridge.inflight['old'][0]
    assert bridge.inflight['old'][1] == (parent,)
    assert not ledger.ready(parent.wire)
    with pytest.raises(ValueError, match='Stale'):
        bridge.apply_receipts(set(), {'old': 1}, {'old': 'wrong'})
    assert sum(b.ref_cnt for b in blocks) == chunks * 16
    bridge.apply_receipts({'old'} if failed else set(), {'old': 1}, {'old': batch})
    helper.receipts({'old': dict(describe(stores[0]), source='protection', batch_id=batch)},
                    {'old'} if failed else set())
    with pytest.raises(ValueError, match='Stale'):
        bridge.apply_receipts(set(), {'old': 1}, {'old': batch})
    assert all(b.ref_cnt == 0 for b in blocks)
    if chunks == 1 and not failed:
        assert not ledger.ready(parent.wire)
        child = next(iter(bridge.pending.values()))
        assert ledger.derived_from[child] == parent
        output = NS(total_num_scheduled_tokens=512, scheduled_new_reqs=[NS(block_ids=[list(range(128))])],
                    scheduled_cached_reqs=NS(new_block_ids=[]))
        assert len(bridge.on_scheduler_step(output).stores_to_submit) == 1
        bridge.apply_receipts(set(), {'old': 1}, {'old': bridge.inflight['old'][0]})
    assert ledger.ready(parent.wire)
    assert helper.pool.get_num_free_blocks() == 128


def test_zero_token_rollback_preserves_original_ticket(combined):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    before = dict(bridge.pending)
    helper.prepare(scheduler)
    assert bridge.commit_protection(helper, 0) == []
    assert bridge.pending == before and not bridge.splits and not ledger.derived_from
    assert all(b.ref_cnt == 0 for b in blocks)


@pytest.mark.parametrize('point', ['registered', 'policy_changed'])
def test_partial_native_commit_never_allows_end_or_receipt_recovery(combined, monkeypatch, point):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.budget = 16
    helper.prepare(scheduler)
    if point == 'registered':
        original = bridge.manager._requests.register_batch
        def broken(*args):
            original(*args)
            raise RuntimeError('injected after register')
        monkeypatch.setattr(bridge.manager._requests, 'register_batch', broken)
    else:
        original = helper.commit
        def broken(*args):
            original(*args)
            raise RuntimeError('injected after policy mutation')
        monkeypatch.setattr(helper, 'commit', broken)
    with pytest.raises(RuntimeError, match='injected'):
        bridge.commit_protection(helper, 1)
    ledger.seal(wire_id(meta))
    assert not ledger.ready(wire_id(meta)) and ledger.submit_ends(lambda _: pytest.fail('END')) == []
    assert bridge.manager._requests.has_in_flight('old')
    assert sum(b.ref_cnt for b in blocks) == 16
    with pytest.raises(RuntimeError, match='reconstruction'):
        bridge.apply_receipts(set(), {'old': 1}, {'old': 'anything'})
    for callback in (lambda: helper.rollback('scheduler_exception'),
                     lambda: helper.prepare(scheduler),
                     lambda: bridge.on_request_reset('old'),
                     lambda: bridge.on_request_arrived('old'),
                     lambda: bridge.on_request_finished('old'),
                     lambda: bridge.add_store_candidate(meta),
                     lambda: bridge._release_session('old')):
        with pytest.raises(RuntimeError, match='reconstruction'):
            callback()
    assert sum(b.ref_cnt for b in blocks) == 16
    assert bridge.manager._requests.has_in_flight('old')


@pytest.mark.parametrize('failed', [False, True])
def test_old_protected_receipt_preserves_successor_pending(combined, failed):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.budget = 16
    helper.prepare(scheduler)
    stores = bridge.commit_protection(helper, 1)
    old_batch = bridge.inflight['old'][0]
    bridge.on_request_reset('old')
    new = replace(meta, request_configs={**meta.request_configs, SEQUENCE_CONFIG: 2})
    ledger.register('old', wire_id(new))
    bridge.on_request_arrived('old')
    bridge.add_store_candidate(new)
    pending = dict(bridge.pending)
    bridge.apply_receipts({'old'} if failed else set(), {'old': 1}, {'old': old_batch})
    helper.receipts({'old': dict(describe(stores[0]), source='protection', batch_id=old_batch)},
                    {'old'} if failed else set())
    assert bridge.pending == pending
    assert 'old' not in helper.policy._broken_prefixes
    assert all(b.ref_cnt == 0 for b in blocks)
    ledger.seal(wire_id(meta))
    assert ledger.ready(wire_id(meta))
    assert ledger.generations[wire_id(new)].operations[next(iter(pending.values()))] == 'pending'


def test_pending_suffix_idle_drop_retains_active_prefix(combined):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.budget = 16
    helper.prepare(scheduler)
    bridge.commit_protection(helper, 1)
    batch = bridge.inflight['old'][0]
    helper.policy.drop_request('old')
    bridge._drop_removed()
    ledger.seal(wire_id(meta))
    assert not ledger.ready(wire_id(meta))
    bridge.apply_receipts(set(), {'old': 1}, {'old': batch})
    assert ledger.ready(wire_id(meta)) and all(b.ref_cnt == 0 for b in blocks)


@pytest.mark.parametrize('mutation', ['wire', 'tokens', 'range', 'blocks', 'suffix_hash', 'capacity'])
def test_preflight_rejection_does_not_mutate_original_reservation(combined, mutation):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.budget = 16
    helper.prepare(scheduler)
    prepared = helper.prepared['old']
    if mutation == 'wire':
        prepared.metadata = replace(prepared.metadata,
            request_configs={**meta.request_configs, SEQUENCE_CONFIG: 2})
    elif mutation == 'tokens':
        prepared.metadata = replace(prepared.metadata,
            op=replace(prepared.metadata.op, token_ids=list(reversed(meta.op.token_ids))))
    elif mutation == 'range':
        prepared.metadata = replace(prepared.metadata, op=replace(prepared.metadata.op, start=16))
    elif mutation == 'blocks':
        prepared.metadata = replace(prepared.metadata, op=replace(prepared.metadata.op, block_ids=[[99]*16]))
    elif mutation == 'suffix_hash':
        prepared.continuation = replace(prepared.continuation, block_hashes={})
    else:
        ledger.operation_capacity = ledger.serial
    before = dict(bridge.pending)
    with pytest.raises((ValueError, RuntimeError)):
        bridge.commit_protection(helper, 1)
    assert bridge.pending == before and not bridge.splits and not bridge.inflight
    assert not bridge.manager._requests.has_in_flight('old')
    helper.rollback('rejected')
    assert all(b.ref_cnt == 0 for b in blocks)


def test_candidate_rejects_unversioned_serving_manager(combined):
    helper, *_ = combined
    helper.connector = NS(_lazy_offload_manager=object())
    with pytest.raises(TypeError, match='Protection-aware'):
        helper.bind(None)


def test_scheduler_exception_does_not_rollback_native_owned_prepared_pins(combined, monkeypatch):
    helper, scheduler, blocks, bridge, ledger, meta = combined
    helper.budget = 16
    register = bridge.manager._requests.register_batch

    def broken(*args):
        register(*args)
        raise RuntimeError('registered before fault')

    monkeypatch.setattr(bridge.manager._requests, 'register_batch', broken)

    class Scheduler:
        def schedule(self):
            return bridge.commit_protection(helper, 1)

    install_protection_scheduler(Scheduler)
    actual = Scheduler()
    actual.__dict__.update(scheduler.__dict__)
    actual.connector = NS(_bounded=helper)
    with pytest.raises(RuntimeError, match='reconstruction') as error:
        actual.schedule()
    assert str(error.value.__context__) == 'registered before fault'
    assert helper.prepared['old'].blocks == blocks[:16]
    assert bridge.manager._requests.has_in_flight('old')
    assert sum(b.ref_cnt for b in blocks) == 16
    ledger.seal(wire_id(meta))
    assert not ledger.ready(wire_id(meta))


@pytest.mark.parametrize('fault', ['none', 'before_second', 'after_second'])
def test_two_actual_prepared_candidates_partial_commit(combined, monkeypatch, fault):
    from vllm import SamplingParams
    from vllm.v1.request import Request
    from vllm.v1.core import kv_cache_utils as utils
    helper, scheduler, blocks, bridge, ledger, meta = combined
    # Build a second real cached prefix without overwriting the first prefix.
    helper.pool.touch(blocks)
    second_blocks = helper.pool.get_new_blocks(16)
    tokens = list(range(3000, 3256))
    request = Request('second', tokens, SamplingParams(max_tokens=1), None,
        block_hasher=utils.get_request_block_hasher(16, helper.hash_fn))
    helper.pool.cache_full_blocks(request, second_blocks, 0, 16, 16, 0)
    helper.pool.free_blocks(second_blocks)
    helper.pool.free_blocks(blocks)
    # Both prefixes face eviction in the real queue used by prepare().
    selected = blocks[:16] + second_blocks
    for block in selected:
        helper.pool.free_block_queue.remove(block)
    helper.pool.free_block_queue.prepend_n(selected)
    bridge.on_request_reset('old')
    first = replace(meta, op=replace(meta.op, end=256, block_ids=[[b.block_id for b in blocks[:16]]]))
    bridge.on_request_arrived('old')
    bridge.add_store_candidate(first)
    bridge.on_request_finished('old')
    second = replace(meta, request_id='second', request_configs={**meta.request_configs, SEQUENCE_CONFIG: 2}, op=replace(meta.op, token_ids=tokens,
        end=256, block_ids=[[b.block_id for b in second_blocks]]))
    ledger.register('second', wire_id(second))
    bridge.on_request_arrived('second')
    bridge.add_store_candidate(second)
    bridge.on_request_finished('second')
    helper.budget = 32
    helper.prepare(scheduler)
    assert set(helper.prepared) == {'old', 'second'}
    assert all(b.ref_cnt == 1 for b in selected)
    order = list(helper.prepared)
    register = bridge.manager._requests.register_batch
    def injected(rid, ids):
        if rid == order[1] and fault == 'before_second':
            raise RuntimeError('second registration fault')
        register(rid, ids)
        if rid == order[1] and fault == 'after_second':
            raise RuntimeError('second registration fault')
    monkeypatch.setattr(bridge.manager._requests, 'register_batch', injected)
    if fault == 'none':
        stores = bridge.commit_protection(helper, 1)
        assert len(stores) == 2
        batches = {rid: value[0] for rid, value in bridge.inflight.items()}
        bridge.apply_receipts(set(), {rid: 1 for rid in order}, batches)
        helper.receipts({m.request_id: dict(describe(m), source='protection',
            batch_id=batches[m.request_id]) for m in stores}, set())
        assert helper.pool.get_num_free_blocks() == 128
        assert all(b.ref_cnt == 0 for b in selected)
    else:
        with pytest.raises(RuntimeError, match='second registration'):
            bridge.commit_protection(helper, 1)
        assert bridge.manager._requests.has_in_flight(order[0])
        assert bridge.manager._requests.has_in_flight(order[1]) == (fault == 'after_second')
        assert set(helper.inflight) == {order[0]} and set(helper.prepared) == {order[1]}
        with pytest.raises(RuntimeError, match='reconstruction'):
            helper.rollback('scheduler_exception')
        assert all(b.ref_cnt == 1 for b in selected)
        for wire in (wire_id(first), wire_id(second)):
            ledger.seal(wire)
            assert not ledger.ready(wire)
