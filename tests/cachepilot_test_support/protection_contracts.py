"""Reusable assertions for production and diagnostic protection helpers."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from cachepilot.runtime.handoff import describe
from cachepilot.integration.lmcache.manager import operation_key
from cachepilot.integration.lmcache.protocol import wire_id


def check_original_lifecycle(case):
    helper, scheduler, blocks, rows = case
    helper.prepare(scheduler)
    assert helper.pool.get_num_free_blocks() == 96 and all(b.ref_cnt == 1 for b in blocks)
    allocated = helper.pool.get_new_blocks(48)
    assert not set(b.block_id for b in allocated) & set(b.block_id for b in blocks)
    stores = helper.commit(256)
    assert len(stores) == 1 and not helper.policy._pending
    assert helper.manager._requests.has_in_flight("old")
    helper.pool.free_blocks(allocated)
    helper.manager.on_store_results(set(), {"old": 1})
    helper.receipts({"old": dict(describe(stores[0]), source="protection", batch_id="b")}, set())
    assert not helper.inflight and helper.pool.get_num_free_blocks() == 128
    assert all(b.ref_cnt == 0 for b in blocks)
    assert [r["event"] for r in rows] == ["protection_prepared", "protection_metadata_committed", "protection_receipt"]


def check_candidate_rejection(case, mutation):
    helper, scheduler, blocks, rows = case
    if mutation == "hash":
        blocks[0].reset_hash()
    elif mutation == "tokens":
        helper.policy._pending["old"][0].store_metadata.op.token_ids[0] = 9876
    elif mutation == "referenced":
        helper.pool.touch([blocks[0]])
    elif mutation == "capacity":
        scheduler.requests["new"].num_tokens_with_spec = 2048
    elif mutation == "active_id":
        scheduler.requests["old"] = scheduler.requests["new"]
    elif mutation == "inflight":
        helper.manager._requests.register_batch("old", [])
    elif mutation == "partial":
        old = helper.policy._pending["old"][0]
        old.store_metadata = replace(old.store_metadata, op=replace(old.store_metadata.op, end=511))
    before = helper.pool.get_num_free_blocks()
    helper.prepare(scheduler)
    assert not helper.prepared and helper.pool.get_num_free_blocks() == before
    if mutation == "capacity":
        fallback = next(r for r in rows if r["event"] == "protection_fallback")
        assert fallback["reason"] == "capacity"
        assert fallback["required_blocks"] == 32 and fallback["available_blocks"] == 32
        assert fallback["free"] < fallback["reserve"] + 32
        assert fallback["acquired_pins"] == 0


def check_generation_receipt(combined, chunks, failed):
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
