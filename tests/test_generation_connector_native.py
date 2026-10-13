"""Real tracker, request finish and worker-output callbacks with fake transport."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest

pytest.importorskip('lmcache')
from vllm import SamplingParams
from vllm.v1.request import Request, RequestStatus
from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPRequestState
from cachepilot.integration.vllm.generation import GenerationLedgerConnector, SchedulerLedgerClient
from cachepilot.runtime.generation import GenerationSessionLedger
from cachepilot.integration.lmcache.manager import GenerationManagerBridge
from cachepilot.integration.vllm.protection import HandoffWorkerMetadata
from cachepilot.runtime.handoff import HandoffLedger, describe
from cachepilot.integration.lmcache.protocol import wire_id, coalesce_owned_store_metadata
from test_bounded_protection import case
from test_generation_rpc_client import Transport


@pytest.fixture
def live(case, monkeypatch):
    from lmcache.integration.vllm import lazy_offload_manager as native
    original = native._coalesce_store_metadata
    monkeypatch.setattr(native, '_coalesce_store_metadata',
        lambda values: coalesce_owned_store_metadata(values, original))
    helper, scheduler, blocks, rows = case
    meta = helper.policy._pending['old'][0].store_metadata
    helper.manager.on_request_reset('old')
    connector = object.__new__(GenerationLedgerConnector)
    connector._generation_ledger = GenerationSessionLedger()
    connector._generation_retrieves = {}
    connector._generation_end_requested = set()
    connector._owned_client, connector._owned_sequence = 7, 0
    connector.request_trackers = {}
    connector.lazy_offload = True
    connector._lazy_offload_manager = GenerationManagerBridge(helper.manager, connector._generation_ledger)
    connector._handoff_record = lambda *args, **kwargs: None
    connector._handoffs = HandoffLedger(connector._handoff_record)
    connector._bounded = None
    transport = Transport()
    client = SchedulerLedgerClient(transport, 7, connector)
    connector.scheduler_adapter = NS(req_clients={'url': client},
        end_session=client.end_session, cleanup_lookup_result=lambda rid: None)
    return connector, client, transport, meta, blocks


@pytest.mark.parametrize('preemptions', [1, 2])
def test_real_callbacks_end_store_only_generations_after_receipt(live, preemptions):
    connector, client, transport, meta, blocks = live
    request = Request('old', list(range(512)), SamplingParams(max_tokens=48), None)
    tracker = connector._get_or_create_request_tracker(request)
    first = replace(meta, request_configs=dict(tracker.request_configs))
    client.lookup(first).set_result(None)
    client.poll_rpc()
    wires = [wire_id(first)]
    for _ in range(preemptions):
        tracker.state = LMCacheMPRequestState.READY
        request.status = RequestStatus.PREEMPTED
        assert connector.get_num_new_matched_tokens(request, 0) == (0, False)
        tracker = connector.request_trackers['old']
        current = replace(meta, request_configs=dict(tracker.request_configs))
        wires.append(wire_id(current))
    assert client.identities['old'] == wires[-1]
    assert client.ledger.lookup_routes['old'] == wires[0]
    manager = connector._lazy_offload_manager
    manager.add_store_candidate(current)
    assert connector.request_finished(request, [b.block_id for b in blocks])[0] is False
    actions = manager.on_scheduler_step(NS(total_num_scheduled_tokens=512,
        scheduled_new_reqs=[NS(block_ids=[list(range(128))])],
        scheduled_cached_reqs=NS(new_block_ids=[])))
    store, = actions.stores_to_submit
    batch_id = manager.inflight['old'][0]
    connector._handoffs.accept(dict(describe(store), batch_id=batch_id), 'test_built')
    assert all(b.ref_cnt == 1 for b in blocks)
    output = NS(kv_cache_events=None, finished_recving=None,
        kv_connector_worker_meta=HandoffWorkerMetadata({'old': 1}, set(), {'old': batch_id}))
    connector.update_connector_output(output)
    ended = [value for name, value in transport.calls if name == 'end_session']
    assert ended == wires
    assert not connector._handoffs.pending and not manager.inflight
    assert all(b.ref_cnt == 0 for b in blocks)


def test_cancel_before_lookup_ack_preserves_original_future(live):
    connector, client, transport, meta, blocks = live
    request = Request('old', list(range(512)), SamplingParams(max_tokens=48), None)
    tracker = connector._get_or_create_request_tracker(request)
    key = replace(meta, request_configs=dict(tracker.request_configs))
    future = client.lookup(key)
    connector.request_finished(request, [])
    assert not any(name == 'end_session' for name, _ in transport.calls)
    future.set_result(None)
    connector._flush_generation_ends()
    assert transport.calls[-1] == ('end_session', wire_id(key))


def test_idle_guard_accepts_new_tracker_identity_without_replacing_lookup_route(live):
    from cachepilot.runtime.idle import finalize_idle_pending
    connector, client, transport, meta, blocks = live
    request = Request('old', list(range(512)), SamplingParams(max_tokens=48), None)
    first = connector._get_or_create_request_tracker(request)
    key = replace(meta, request_configs=dict(first.request_configs))
    client.lookup(key).set_result(None)
    client.poll_rpc()
    first.state = LMCacheMPRequestState.READY
    request.status = RequestStatus.PREEMPTED
    connector.get_num_new_matched_tokens(request, 0)
    second = connector.request_trackers['old']
    connector._lazy_offload_manager.add_store_candidate(
        replace(meta, request_configs=dict(second.request_configs)))
    connector.request_finished(request, [])
    scheduler = NS(requests={}, running=[], waiting=[], skipped_waiting=[], deferred_frees={})
    assert finalize_idle_pending(connector, scheduler) == 1
    assert len([n for n, _ in transport.calls if n == 'end_session']) == 2
    assert client.ledger.lookup_routes['old'] == wire_id(key)
    assert not connector._lazy_offload_manager.pending
    assert all(b.ref_cnt == 0 for b in blocks)


@pytest.mark.parametrize('failed', [False, True])
def test_real_raw_id_reuse_with_old_inflight_receipt(live, failed):
    from cachepilot.runtime.idle import finalize_idle_pending
    connector, client, transport, meta, blocks = live
    first = Request('old', list(range(512)), SamplingParams(max_tokens=48), None)
    tracker = connector._get_or_create_request_tracker(first)
    old = replace(meta, request_configs=dict(tracker.request_configs))
    client.lookup(old).set_result(None)
    client.poll_rpc()
    manager = connector._lazy_offload_manager
    manager.add_store_candidate(old)
    connector.request_finished(first, [])
    actions = manager.on_scheduler_step(NS(total_num_scheduled_tokens=512,
        scheduled_new_reqs=[NS(block_ids=[list(range(128))])],
        scheduled_cached_reqs=NS(new_block_ids=[])))
    store, = actions.stores_to_submit
    batch_id = manager.inflight['old'][0]
    connector._handoffs.accept(dict(describe(store), batch_id=batch_id), 'test_built')
    second = Request('old', list(range(512)), SamplingParams(max_tokens=48), None)
    tracker = connector._get_or_create_request_tracker(second)
    new = replace(meta, request_configs=dict(tracker.request_configs))
    client.lookup(new).set_result(None)
    client.poll_rpc()
    manager.add_store_candidate(new)
    pending = dict(manager.pending)
    receipt = HandoffWorkerMetadata({'old': 1}, {'old'} if failed else set(), {'old': batch_id})
    output = NS(kv_cache_events=None, finished_recving=None, kv_connector_worker_meta=receipt)
    connector.update_connector_output(output)
    assert manager.pending == pending
    assert not any(name == 'end_session' for name, _ in transport.calls)
    assert all(b.ref_cnt == 0 for b in blocks)
    with pytest.raises(ValueError):
        connector.update_connector_output(output)
    assert manager.pending == pending
    connector.request_finished(second, [])
    finalize_idle_pending(connector, NS(requests={}, running=[], waiting=[],
        skipped_waiting=[], deferred_frees={}))
    assert [value for name, value in transport.calls if name == 'end_session'] == [
        wire_id(old), wire_id(new)]
    assert connector.generation_snapshot()['pending_rpc'] == 0
