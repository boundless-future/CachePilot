"""Connector callback fixture; model work and transport are stubbed."""
import pytest
from types import SimpleNamespace as NS
from .pool import case
from .rpc import Transport
from cachepilot.integration.lmcache.protection import GenerationProtectionBridge, ProtectionGenerationLedger, GenerationBoundedProtection
from cachepilot.integration.vllm.generation_protection import GenerationProtectionConnector
from cachepilot.integration.vllm.generation import SchedulerLedgerClient
from cachepilot.integration.lmcache.protocol import wire_id
from cachepilot.runtime.handoff import HandoffLedger, ProtectionConfig

@pytest.fixture(params=['original', 'shared'])
def wired(case, request):
    if request.param == 'shared':
        from cachepilot.integration.vllm.shared_prefix import SharedPrefixConnector, SharedPrefixServiceProtection
        connector_type, helper_type = SharedPrefixConnector, SharedPrefixServiceProtection
    else:
        connector_type, helper_type = GenerationProtectionConnector, GenerationBoundedProtection
    old_helper, scheduler, blocks, rows = case
    meta = old_helper.policy._pending['old'][0].store_metadata
    old_helper.manager.on_request_reset('old')
    obj = object.__new__(connector_type)
    obj._generation_ledger = ProtectionGenerationLedger()
    obj._generation_retrieves, obj._generation_end_requested = {}, set()
    obj._lazy_offload_manager = GenerationProtectionBridge(old_helper.manager, obj._generation_ledger)
    obj._handoff_record = lambda *a, **k: None
    obj._handoffs = HandoffLedger(obj._handoff_record)
    obj._handoff_serial, obj._bound_handoffs = 0, []
    obj._protection_config = ProtectionConfig(True, 1)
    obj.lazy_offload = True
    transport = Transport()
    client = SchedulerLedgerClient(transport, 1, obj)
    obj.scheduler_adapter = NS(req_clients={'url': client}, end_session=client.end_session,
                               lmcache_tokens_per_chunk=256)
    helper = object.__new__(helper_type)
    helper.__dict__.update(old_helper.__dict__)
    helper.connector, helper.budget = obj, 16
    obj._bounded = helper
    class FullAttentionManager:
        pass
    kv = NS(coordinator=NS(single_type_managers=[FullAttentionManager()]),
            block_pool=helper.pool, allocate_slots=lambda *a, **k: None)
    helper.bind(kv)
    obj._generation_ledger.register('old', wire_id(meta))
    obj._lazy_offload_manager.on_request_arrived('old')
    obj._lazy_offload_manager.add_store_candidate(meta)
    obj._lazy_offload_manager.on_request_finished('old')
    # Preserve real native build_connector_meta and update_connector_output;
    # this fixture has no model requests or block-allocation reporting client.
    for method in ('_process_retrieve_requests', '_process_new_requests',
                   '_process_cached_requests', '_report_block_allocation_deltas'):
        setattr(obj, method, lambda *a: None)
    return obj, helper, scheduler, blocks, transport, meta


def step(tokens=512):
    return NS(total_num_scheduled_tokens=tokens, preempted_req_ids=set(),
        scheduled_new_reqs=[NS(block_ids=[list(range(128))])],
        scheduled_cached_reqs=NS(new_block_ids=[]))
