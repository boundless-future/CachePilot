"""Real tracker/BlockPool replay with explicit ledger lifecycle handoff.

This is a boundary contract, not installed connector wiring. The test supplies
the producer seal and terminal tickets; real callbacks must still do that.
"""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest

pytest.importorskip('lmcache')
from vllm import SamplingParams
from vllm.v1.request import Request, RequestStatus
from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPRequestState
from cachepilot.integration.vllm.owned_service import OwnedServiceConnector
from cachepilot.runtime.generation import LedgerRequestClient
from cachepilot.integration.lmcache.protocol import wire_id
from cachepilot_test_support.pool import case
from cachepilot_test_support.rpc import Client


def test_preempted_store_only_generation_has_its_own_end_after_manager_receipt(case):
    helper, scheduler, blocks, rows = case
    manager = helper.manager
    metadata = helper.policy._pending['old'][0].store_metadata
    manager.on_request_reset('old')
    transport = Client()
    client = LedgerRequestClient(transport)
    connector = object.__new__(OwnedServiceConnector)
    connector._owned_client, connector._owned_sequence = 7, 0
    connector.request_trackers = {}
    connector.lazy_offload = True
    connector._lazy_offload_manager = manager
    # Native PREEMPTED bypass must not call the experimental client.
    connector.scheduler_adapter = NS(req_clients={'url': client})
    request = Request('old', list(range(512)), SamplingParams(max_tokens=48), None)
    first = connector._get_or_create_request_tracker(request)
    first_key = replace(metadata, request_configs=dict(first.request_configs))
    client.lookup(first_key)
    lookup_ticket, = client.tickets.values()
    client.terminal(lookup_ticket)  # recorded transport has no real ACK
    first.state = LMCacheMPRequestState.READY
    request.status = RequestStatus.PREEMPTED
    assert connector.get_num_new_matched_tokens(request, 0) == (0, False)
    second = connector.request_trackers['old']
    new_key = replace(metadata, request_configs=dict(second.request_configs))
    manager.add_store_candidate(new_key)
    manager.on_request_finished('old')
    actions = manager.on_scheduler_step(NS(total_num_scheduled_tokens=512,
        scheduled_new_reqs=[NS(block_ids=[list(range(128))])],
        scheduled_cached_reqs=NS(new_block_ids=[])))
    store, = actions.stores_to_submit
    ticket = client.reserve_store(store)
    assert ticket.wire == wire_id(new_key) != wire_id(first_key)
    client.submit_store(ticket)
    assert client.end_generation(wire_id(new_key)) == []
    results = manager.on_store_results(set(), {'old': 1})
    assert results.sessions_to_end == ['old']
    client.terminal(ticket)
    # Exact predecessor producer seal permits both generations to end.
    assert client.end_generation(wire_id(first_key)) == [wire_id(first_key), wire_id(new_key)]
    assert transport.calls == [('lookup', wire_id(first_key)),
        ('store', wire_id(new_key)), ('end_session', wire_id(first_key)),
        ('end_session', wire_id(new_key))]
    assert all(block.ref_cnt == 0 for block in blocks)
