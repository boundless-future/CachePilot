"""Real native metadata/receipt callbacks; transport and model work are stubbed."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pickle
import pytest

pytest.importorskip('lmcache')
from test_bounded_protection import case
from test_generation_rpc_client import Transport
from cachepilot.integration.vllm.generation_protection import GenerationProtectionConnector
from cachepilot.integration.vllm.generation import GenerationLedgerConnector, SchedulerLedgerClient
from cachepilot.integration.lmcache.protection import (
    GenerationProtectionBridge, ProtectionGenerationLedger, GenerationBoundedProtection)
from cachepilot.runtime.handoff import HandoffLedger, ProtectionConfig
from cachepilot.integration.vllm.protection import HandoffWorkerMetadata, HandoffObservedConnector
from cachepilot.integration.vllm.owned_service import OwnedServiceConnector
from cachepilot.integration.lmcache.protocol import wire_id
from cachepilot.integration.lmcache.protocol import SEQUENCE_CONFIG
from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPWorkerMetadata


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


@pytest.mark.parametrize('chunks', [1, 2])
@pytest.mark.parametrize('failed', [False, True])
def test_bound_native_build_worker_envelope_and_original_receipt(wired, monkeypatch, chunks, failed):
    obj, helper, scheduler, blocks, transport, meta = wired
    helper.budget = chunks * 16
    helper.prepare(scheduler)
    built = obj.build_connector_meta(step())
    batch, = built.cachepilot_handoffs_v1
    assert batch['source'] == 'protection'
    assert batch['batch_id'] == obj._lazy_offload_manager.inflight['old'][0]
    assert built.requests[0].op.end == chunks * 256
    assert sum(b.ref_cnt for b in blocks) == chunks * 16
    assert not transport.calls
    # Exercise actual worker envelope methods with transport/native copy stubbed.
    worker = object.__new__(HandoffObservedConnector)
    worker._handoff_record = lambda *a, **k: None
    worker._handoffs = HandoffLedger(worker._handoff_record)
    worker._bound_handoffs = []
    monkeypatch.setattr(OwnedServiceConnector, 'bind_connector_metadata', lambda *a: None)
    monkeypatch.setattr(OwnedServiceConnector, 'wait_for_save', lambda *a: None)
    native = LMCacheMPWorkerMetadata({'old': 1}, {'old'} if failed else set())
    monkeypatch.setattr(OwnedServiceConnector, 'build_connector_worker_meta', lambda *a: native)
    worker.bind_connector_metadata(pickle.loads(pickle.dumps(built)))
    worker.wait_for_save()
    receipt = pickle.loads(pickle.dumps(worker.build_connector_worker_meta()))
    bad = HandoffWorkerMetadata({'old': 1}, set(), {'old': 'wrong'})
    with pytest.raises(ValueError):
        obj.update_connector_output(NS(kv_cache_events=None, finished_recving=None,
                                       kv_connector_worker_meta=bad))
    assert sum(b.ref_cnt for b in blocks) == chunks * 16
    output = NS(kv_cache_events=None, finished_recving=None, kv_connector_worker_meta=receipt)
    obj.update_connector_output(output)
    assert not helper.inflight and all(b.ref_cnt == 0 for b in blocks)
    with pytest.raises(ValueError):
        obj.update_connector_output(output)
    if chunks == 1 and not failed:
        assert not transport.calls
        suffix = obj.build_connector_meta(step())
        assert suffix.requests[0].op.start == 256
        assert suffix.cachepilot_handoffs_v1[0]['source'] == 'default_manager'
        obj.update_connector_output(NS(kv_cache_events=None, finished_recving=None,
            kv_connector_worker_meta=HandoffWorkerMetadata({'old': 1}, set(),
                {'old': suffix.cachepilot_handoffs_v1[0]['batch_id']})))
    assert transport.calls == [('end_session', wire_id(meta))]
    assert helper.pool.get_num_free_blocks() == 128
    snapshot = obj.generation_snapshot()
    assert snapshot['inflight_store'] == snapshot['pending_store'] == 0
    assert len(snapshot['derived_tickets']) == (chunks == 1)
    assert snapshot['generations'][0]['end_state'] == 'submitted'


def test_explicit_opt_in_and_original_zero_guard():
    new = object.__new__(GenerationProtectionConnector)
    with pytest.raises(ValueError, match='opt-in'):
        new._validate_protection({'cachepilot.protection.max_chunks': 1})
    new._validate_protection({'cachepilot.generation_protection': True})
    old = object.__new__(GenerationLedgerConnector)
    with pytest.raises(ValueError, match='zero protection'):
        old._validate_protection({'cachepilot.protection.max_chunks': 1})


def test_zero_token_bound_step_preserves_reservation(wired):
    obj, helper, scheduler, blocks, transport, meta = wired
    before = dict(obj._lazy_offload_manager.pending)
    helper.prepare(scheduler)
    assert not obj.build_connector_meta(step(0)).requests
    assert obj._lazy_offload_manager.pending == before
    assert not helper.prepared and not transport.calls
    assert all(b.ref_cnt == 0 for b in blocks)


def test_poison_blocks_connector_end_pump(wired, monkeypatch):
    obj, helper, scheduler, blocks, transport, meta = wired
    helper.prepare(scheduler)
    register = helper.manager._requests.register_batch
    def broken(*args):
        register(*args)
        raise RuntimeError('native registered fault')
    monkeypatch.setattr(helper.manager._requests, 'register_batch', broken)
    with pytest.raises(RuntimeError, match='native registered'):
        obj.build_connector_meta(step())
    for callback in (obj._flush_generation_ends, lambda: helper.rollback('exception')):
        with pytest.raises(RuntimeError, match='reconstruction'):
            callback()
    assert obj.generation_snapshot()['protection_poisoned']
    assert not transport.calls and sum(b.ref_cnt for b in blocks) == 16


def test_fault_after_commit_before_envelope_retains_ownership(wired, monkeypatch):
    obj, helper, scheduler, blocks, transport, meta = wired
    helper.prepare(scheduler)
    def broken(*args):
        raise RuntimeError('metadata envelope fault')
    monkeypatch.setattr(obj, '_apply_protection', broken)
    with pytest.raises(RuntimeError, match='envelope fault'):
        obj.build_connector_meta(step())
    assert helper.manager._requests.has_in_flight('old')
    assert helper.inflight and not helper.prepared
    assert sum(b.ref_cnt for b in blocks) == 16
    with pytest.raises(RuntimeError, match='reconstruction'):
        obj._flush_generation_ends()
    assert not transport.calls


@pytest.mark.parametrize('failed', [False, True])
def test_two_resets_before_old_protected_receipt_preserve_latest_pending(wired, failed):
    obj, helper, scheduler, blocks, transport, meta = wired
    helper.prepare(scheduler)
    built = obj.build_connector_meta(step())
    manager = obj._lazy_offload_manager
    wires = [wire_id(meta)]
    for sequence in (2, 3):
        manager.on_request_reset('old')
        successor = replace(meta, request_configs={**meta.request_configs, SEQUENCE_CONFIG: sequence})
        wires.append(wire_id(successor))
        obj._generation_ledger.register('old', wires[-1])
        manager.on_request_arrived('old')
        manager.add_store_candidate(successor)
    pending = dict(manager.pending)
    obj.update_connector_output(NS(kv_cache_events=None, finished_recving=None,
        kv_connector_worker_meta=HandoffWorkerMetadata({'old': 1}, {'old'} if failed else set(),
            {'old': built.cachepilot_handoffs_v1[0]['batch_id']})))
    assert manager.pending == pending and not helper.inflight
    assert not transport.calls and all(b.ref_cnt == 0 for b in blocks)
    manager.on_request_finished('old')
    from cachepilot.runtime.idle import finalize_idle_pending
    finalize_idle_pending(obj, NS(requests={}, running=[], waiting=[],
                                 skipped_waiting=[], deferred_frees={}))
    assert transport.calls == [('end_session', wire) for wire in wires]


def test_constructor_factories_select_candidate_without_changing_zero_class(case, monkeypatch):
    from cachepilot.integration.vllm.idle import IdleFinalizeConnector
    from cachepilot.runtime.protection import BoundedProtection
    helper, _, _, _ = case
    helper.manager.on_request_reset('old')
    monkeypatch.setattr(BoundedProtection, '__init__', lambda self, connector, config: None)
    def base_init(self, config, role, kv_cache_config=None):
        self._lazy_offload_manager = helper.manager
        self._owned_client = 1
        self.scheduler_adapter = NS(req_clients={'url': NS(client=Transport())})
        self._bounded = self._make_bounded_protection(config)
    monkeypatch.setattr(IdleFinalizeConnector, '__init__', base_init)
    config = NS(kv_transfer_config=NS(kv_connector_extra_config={
        'cachepilot.generation_ledger': True, 'cachepilot.generation_protection': True,
        'cachepilot.protection.max_chunks': 1}), scheduler_config=NS(async_scheduling=False),
        speculative_config=None, lora_config=None)
    obj = GenerationProtectionConnector(config, NS(name='SCHEDULER'))
    assert isinstance(obj._generation_ledger, ProtectionGenerationLedger)
    assert isinstance(obj._lazy_offload_manager, GenerationProtectionBridge)
    assert isinstance(obj._bounded, GenerationBoundedProtection)
    assert isinstance(obj.scheduler_adapter.req_clients['url'], SchedulerLedgerClient)
    with pytest.raises(ValueError, match='zero protection'):
        GenerationLedgerConnector(config, NS(name='SCHEDULER'))


def test_receipt_callback_fault_after_native_free_does_not_retry_free(wired, monkeypatch):
    obj, helper, scheduler, blocks, transport, meta = wired
    helper.prepare(scheduler)
    built = obj.build_connector_meta(step())
    def broken(*args):
        raise RuntimeError('helper receipt fault')
    monkeypatch.setattr(helper, 'receipts', broken)
    output = NS(kv_cache_events=None, finished_recving=None,
        kv_connector_worker_meta=HandoffWorkerMetadata({'old': 1}, set(),
            {'old': built.cachepilot_handoffs_v1[0]['batch_id']}))
    with pytest.raises(RuntimeError, match='helper receipt fault'):
        obj.update_connector_output(output)
    assert all(b.ref_cnt == 0 for b in blocks)
    assert helper.inflight and obj._handoffs.pending
    with pytest.raises(RuntimeError, match='reconstruction'):
        obj.update_connector_output(output)
    assert helper.pool.get_num_free_blocks() == 128
    assert not transport.calls


@pytest.mark.parametrize('write_failure', [False, True])
def test_poisoned_shutdown_preserves_state_and_original_error(wired, monkeypatch, tmp_path, write_failure):
    import json
    obj, helper, scheduler, blocks, transport, meta = wired
    obj._handoff_role = 'SCHEDULER'
    helper.prepare(scheduler)
    obj.build_connector_meta(step())
    obj._lazy_offload_manager.poison(RuntimeError('injected commit fault'))
    target = tmp_path / 'summary.json'
    if write_failure:
        target.mkdir()
    monkeypatch.setenv('CACHEPILOT_GENERATION_SUMMARY_PATH', str(target))
    with pytest.raises(RuntimeError, match='reconstruction') as error:
        obj.shutdown()
    assert helper.pool.get_num_free_blocks() == 112
    assert sum(b.ref_cnt for b in blocks) == 16
    assert not transport.calls
    if write_failure:
        assert any('preserve shutdown evidence' in note for note in error.value.__notes__)
    else:
        saved = json.loads(target.read_text())
        assert saved['shutdown_complete'] is False
        assert saved['protected_inflight_blocks'] == 16
        assert saved['prepared_blocks'] == 0
        assert saved['inflight_store'] == 1 and saved['pending_store'] == 1
        assert 'injected commit fault' in saved['protection_poisoned']
        assert not target.with_name(target.name + '.tmp').exists()
