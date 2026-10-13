from dataclasses import replace
from types import SimpleNamespace as NS
import pytest

pytest.importorskip('lmcache')
from cachepilot.integration.lmcache.manager import GenerationManagerBridge
from cachepilot.runtime.generation import GenerationSessionLedger
from cachepilot.integration.lmcache.protocol import wire_id, SEQUENCE_CONFIG, coalesce_owned_store_metadata
from cachepilot_test_support.pool import case


@pytest.fixture
def bridge_case(case, monkeypatch):
    from lmcache.integration.vllm import lazy_offload_manager as native
    original = native._coalesce_store_metadata
    monkeypatch.setattr(native, '_coalesce_store_metadata',
        lambda values: coalesce_owned_store_metadata(values, original))
    helper, scheduler, blocks, rows = case
    metadata = helper.policy._pending['old'][0].store_metadata
    helper.manager.on_request_reset('old')
    ledger = GenerationSessionLedger()
    bridge = GenerationManagerBridge(helper.manager, ledger)
    ledger.register('old', wire_id(metadata))
    bridge.on_request_arrived('old')
    return bridge, ledger, metadata, helper, blocks


def drain(bridge):
    return bridge.on_scheduler_step(NS(total_num_scheduled_tokens=512,
        scheduled_new_reqs=[NS(block_ids=[list(range(128))])],
        scheduled_cached_reqs=NS(new_block_ids=[])))


@pytest.mark.parametrize('failed', [False, True])
def test_old_batch_receipt_after_reset_preserves_new_pending(bridge_case, failed):
    bridge, ledger, old, helper, blocks = bridge_case
    bridge.add_store_candidate(old)
    assert len(drain(bridge).stores_to_submit) == 1
    batch_id, tickets = bridge.inflight['old']
    assert all(b.ref_cnt == 1 for b in blocks)
    bridge.on_request_reset('old')
    new = replace(old, request_configs={**old.request_configs, SEQUENCE_CONFIG: 2})
    ledger.register('old', wire_id(new))
    bridge.on_request_arrived('old')
    bridge.add_store_candidate(new)
    before = dict(bridge.pending)
    bridge.apply_receipts({'old'} if failed else set(), {'old': 1}, {'old': batch_id})
    assert bridge.pending == before
    assert all(ledger.generations[t.wire].operations[t] == 'terminal' for t in tickets)
    assert all(b.ref_cnt == 0 for b in blocks)
    assert 'old' not in helper.policy._broken_prefixes
    assert ledger.generations[wire_id(new)].operations[next(iter(before.values()))] == 'pending'


def test_two_resets_drop_only_unsubmitted_generations(bridge_case):
    bridge, ledger, meta, helper, blocks = bridge_case
    for sequence in (1, 2, 3):
        key = replace(meta, request_configs={**meta.request_configs, SEQUENCE_CONFIG: sequence})
        ledger.register('old', wire_id(key))
        bridge.add_store_candidate(key)
        bridge.on_request_reset('old')
        bridge.on_request_arrived('old')
    assert not bridge.pending and not bridge.inflight
    assert all(state == 'dropped' for g in ledger.generations.values()
               for state in g.operations.values())
    assert all(b.ref_cnt == 0 for b in blocks)


def test_wrong_duplicate_and_unversioned_receipts_do_not_free_pins(bridge_case):
    bridge, ledger, meta, helper, blocks = bridge_case
    bridge.add_store_candidate(meta)
    drain(bridge)
    batch_id = bridge.inflight['old'][0]
    with pytest.raises(ValueError, match='Stale'):
        bridge.apply_receipts(set(), {'old': 1}, {'old': 'wrong'})
    with pytest.raises(RuntimeError, match='Unversioned'):
        bridge.on_store_results(set(), {'old': 1})
    assert all(b.ref_cnt == 1 for b in blocks)
    bridge.apply_receipts(set(), {'old': 1}, {'old': batch_id})
    with pytest.raises(ValueError, match='Stale'):
        bridge.apply_receipts(set(), {'old': 1}, {'old': batch_id})
    assert all(b.ref_cnt == 0 for b in blocks)


def test_multiple_chunks_coalesce_into_one_exact_generation_batch(bridge_case):
    bridge, ledger, meta, helper, blocks = bridge_case
    for start, end in ((0, 256), (256, 512)):
        bridge.add_store_candidate(replace(meta, op=replace(meta.op, start=start,
            end=end, block_ids=[[b.block_id for b in blocks[start//16:end//16]]])))
    bridge.on_request_finished('old')
    assert len(drain(bridge).stores_to_submit) == 1
    batch_id, tickets = bridge.inflight['old']
    assert len(tickets) == 2
    ledger.seal(wire_id(meta))
    assert not ledger.ready(wire_id(meta))
    bridge.apply_receipts(set(), {'old': 1}, {'old': batch_id})
    assert ledger.ready(wire_id(meta))
    assert all(b.ref_cnt == 0 for b in blocks)
