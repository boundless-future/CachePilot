"""Native callback ownership when another owner already holds prefix blocks."""
from types import SimpleNamespace as NS
import pytest
pytest.importorskip('lmcache')
from test_bounded_protection import case
from test_generation_protection_connector import wired, step
from cachepilot.integration.vllm.shared_prefix import SharedPrefixConnector, SharedPrefixServiceProtection
from cachepilot.runtime.shared_prefix import SharedPrefixProtection
from cachepilot.integration.vllm.protection import HandoffWorkerMetadata


@pytest.mark.parametrize('action', ['success', 'failure', 'zero', 'cancel_before_receipt'])
def test_shared_owner_through_bound_callbacks(wired, action):
    obj, helper, scheduler, blocks, transport, meta = wired
    shared = blocks[:8]
    helper.pool.touch(shared)
    helper.prepare(scheduler)
    if not isinstance(helper, SharedPrefixServiceProtection):
        assert not helper.prepared
        helper.pool.free_blocks(shared)
        return
    assert all(b.ref_cnt == 2 for b in shared)
    built = obj.build_connector_meta(step(0 if action == 'zero' else 512))
    if action != 'zero':
        batch, = built.cachepilot_handoffs_v1
        if action == 'cancel_before_receipt':
            helper.pool.free_blocks(shared)
        output = NS(kv_cache_events=None, finished_recving=None,
            kv_connector_worker_meta=HandoffWorkerMetadata({'old': 1},
                {'old'} if action == 'failure' else set(), {'old': batch['batch_id']}))
        obj.update_connector_output(output)
        with pytest.raises(ValueError):
            obj.update_connector_output(output)
    assert all(b.ref_cnt == (0 if action == 'cancel_before_receipt' else 1) for b in shared)
    assert not helper.prepared and not helper.inflight
    if action != 'cancel_before_receipt':
        helper.pool.free_blocks(shared)
    assert helper.pool.get_num_free_blocks() == 128


def test_admission_and_probe_mro():
    obj = object.__new__(SharedPrefixConnector)
    with pytest.raises(ValueError, match='shared-prefix'):
        obj._validate_protection({'cachepilot.generation_protection': True})
    obj._validate_protection({'cachepilot.generation_protection': True,
                             'cachepilot.shared_prefix_protection': True})
    with pytest.raises(RuntimeError, match='independent service'):
        object.__new__(SharedPrefixProtection).bind(None)
    from scripts import shared_prefix_probe as probes
    for name in ('SharedPrefixProbeConnector', 'SharedPrefixReceiptProbeConnector',
                 'SharedPrefixCancelProbeConnector', 'SharedPrefixPreemptionProbeConnector'):
        cls = getattr(probes, name)
        assert cls._make_bounded_protection is SharedPrefixConnector._make_bounded_protection
        assert cls._validate_protection is SharedPrefixConnector._validate_protection


@pytest.mark.parametrize('fault', ['none', 'before_second', 'after_second'])
@pytest.mark.parametrize('wired', ['shared'], indirect=True)
def test_two_shared_candidates_through_bound_native_callback(wired, monkeypatch, fault):
    from dataclasses import replace
    from cachepilot.integration.lmcache.protocol import wire_id, SEQUENCE_CONFIG
    obj, helper, scheduler, blocks, transport, meta = wired
    assert isinstance(helper, SharedPrefixServiceProtection)
    bridge = obj._lazy_offload_manager
    bridge.on_request_reset('old')
    first = replace(meta, op=replace(meta.op, end=256, block_ids=[[b.block_id for b in blocks[:16]]]))
    bridge.on_request_arrived('old')
    bridge.add_store_candidate(first)
    bridge.on_request_finished('old')
    second = replace(first, request_id='second',
        request_configs={**first.request_configs, SEQUENCE_CONFIG: 2})
    obj._generation_ledger.register('second', wire_id(second))
    bridge.on_request_arrived('second')
    bridge.add_store_candidate(second)
    bridge.on_request_finished('second')
    helper.pool.touch(blocks[:1])
    helper.budget = 32
    helper.prepare(scheduler)
    assert set(helper.prepared) == {'old', 'second'}
    assert [b.ref_cnt for b in blocks[:16]] == [3] + [2] * 15
    order = list(helper.prepared)
    register = bridge.manager._requests.register_batch
    def injected(rid, ids):
        if rid == order[1] and fault == 'before_second':
            raise RuntimeError('second registration fault')
        register(rid, ids)
        if rid == order[1] and fault == 'after_second':
            raise RuntimeError('second registration fault')
    monkeypatch.setattr(bridge.manager._requests, 'register_batch', injected)
    if fault != 'none':
        with pytest.raises(RuntimeError, match='second registration'):
            obj.build_connector_meta(step())
        assert bridge.manager._requests.has_in_flight(order[0])
        assert bridge.manager._requests.has_in_flight(order[1]) == (fault == 'after_second')
        with pytest.raises(RuntimeError, match='reconstruction'):
            helper.rollback('exception')
        with pytest.raises(RuntimeError, match='reconstruction'):
            obj._flush_generation_ends()
        assert [b.ref_cnt for b in blocks[:16]] == [3] + [2] * 15
        assert not transport.calls
    else:
        built = obj.build_connector_meta(step())
        batches = {b['request_id']:b['batch_id'] for b in built.cachepilot_handoffs_v1}
        obj.update_connector_output(NS(kv_cache_events=None, finished_recving=None,
            kv_connector_worker_meta=HandoffWorkerMetadata({r:1 for r in batches}, {'second'}, batches)))
        assert [b.ref_cnt for b in blocks[:16]] == [1] + [0] * 15
        assert not helper.inflight
        helper.pool.free_blocks(blocks[:1])
        assert helper.pool.get_num_free_blocks() == 128
