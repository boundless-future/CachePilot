"""Real tracker/manager reproduction of lookup-bypassed preemption identity.

This characterizes the unfixed stack. Transport is recorded, no GPU or RPC.
"""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest

pytest.importorskip('lmcache')
from vllm import SamplingParams
from vllm.v1.request import Request, RequestStatus
from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPRequestState
from cachepilot.integration.vllm.owned_service import OwnedServiceConnector
from cachepilot.integration.lmcache.protocol import OwnedRequestClient, wire_id
from cachepilot.runtime.idle import finalize_idle_pending
from test_bounded_protection import case
from test_idle_finalize_native import pool_state
from test_owned_service_protocol import Client


@pytest.mark.parametrize('outcome', ['pending', 'store'])
def test_preemption_recreates_tracker_without_advancing_lookup_mapping(case, outcome):
    helper, scheduler, blocks, rows = case
    manager = helper.manager
    original_meta = helper.policy._pending['old'][0].store_metadata
    manager.on_request_reset('old')
    transport = Client()
    client = OwnedRequestClient(transport, 7)
    connector = object.__new__(OwnedServiceConnector)
    connector._owned_client, connector._owned_sequence = 7, 0
    connector.request_trackers = {}
    connector.lazy_offload = True
    connector._lazy_offload_manager = manager
    connector.scheduler_adapter = NS(req_clients={'url':client},
        end_session=client.end_session)
    request = Request('old', list(range(512)), SamplingParams(max_tokens=48), None)
    first = connector._get_or_create_request_tracker(request)
    first_key = replace(original_meta, request_configs=dict(first.request_configs))
    client.lookup(first_key)
    first.state = LMCacheMPRequestState.READY
    request.status = RequestStatus.PREEMPTED
    before_calls = list(transport.calls)
    # Invoke the actual native early return, not a simulated tracker swap.
    assert connector.get_num_new_matched_tokens(request, 0) == (0, False)
    second = connector.request_trackers['old']
    assert second is not first and transport.calls == before_calls
    current_meta = replace(original_meta, request_configs=dict(second.request_configs))
    assert wire_id(current_meta) != wire_id(first_key)
    assert client.identities['old'] == wire_id(first_key)
    manager.add_store_candidate(current_meta)
    if outcome == 'pending':
        manager.on_request_finished('old')
        connector._bounded, connector._handoffs = None, NS(pending={})
        connector._handoff_record = lambda *args, **kwargs: None
        scheduler.requests = {}
        scheduler.running = scheduler.waiting = scheduler.skipped_waiting = []
        scheduler.deferred_frees = {}
        before = pool_state(helper.pool)
        with pytest.raises(RuntimeError, match='current wire identity'):
            finalize_idle_pending(connector, scheduler)
        assert pool_state(helper.pool) == before
        assert helper.policy.has_pending_request('old')
        assert transport.calls == before_calls
    else:
        # Exercise the real manager's submitted batch and receipt path.
        manager.on_request_finished('old')
        stores = manager.on_scheduler_step(NS(total_num_scheduled_tokens=512,
            scheduled_new_reqs=[NS(block_ids=[list(range(128))])],
            scheduled_cached_reqs=NS(new_block_ids=[]))).stores_to_submit
        assert len(stores) == 1
        worker = OwnedRequestClient(transport)
        worker.store(stores[0])
        actions = manager.on_store_results(set(), {'old':1})
        assert actions.sessions_to_end == ['old']
        for rid in actions.sessions_to_end:
            client.end_session(rid)
        assert ('store', wire_id(current_meta)) in transport.calls
        assert ('end_session', wire_id(first_key)) in transport.calls
        assert ('end_session', wire_id(current_meta)) not in transport.calls
        assert all(b.ref_cnt == 0 for b in blocks)
