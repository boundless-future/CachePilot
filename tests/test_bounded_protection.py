"""Real BlockPool/LMCache manager contracts; no model server or CUDA needed."""
from cachepilot_test_support.protection_contracts import check_original_lifecycle, check_candidate_rejection
from cachepilot_test_support.pool import case
from types import SimpleNamespace as NS
from dataclasses import replace
import pytest

pytest.importorskip("lmcache")
from lmcache.integration.vllm.lazy_offload_manager import LazyOffloadManager
from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPRequestMetadata, LoadStoreOp
from lmcache.v1.multiprocess.token_hasher import TokenHasher
from vllm.v1.core import kv_cache_utils as utils
from vllm.v1.core.block_pool import BlockPool
from vllm.utils.hashing import get_hash_fn_by_name
from cachepilot.runtime.protection import BoundedProtection, conservative_reserve, install_protection_scheduler
from cachepilot.runtime.handoff import describe
from cachepilot.integration.lmcache.protocol import CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG
from cachepilot.runtime.prefix_proof import build_proof


def test_pin_precedes_allocator_and_original_receipt_releases_once(case):
    check_original_lifecycle(case)


@pytest.mark.parametrize("mutation", ["hash", "tokens", "referenced", "capacity", "active_id", "inflight", "partial"])
def test_invalid_or_unsupported_candidate_is_never_pinned(case, mutation):
    check_candidate_rejection(case, mutation)


def test_zero_token_step_restores_pending_and_capacity(case):
    helper, scheduler, blocks, rows = case
    pending = helper.policy._pending["old"][0]
    helper.prepare(scheduler)
    assert helper.commit(0) == []
    assert helper.pool.get_num_free_blocks() == 128
    assert helper.policy._pending["old"][0] is pending
    assert not helper.manager._requests.has_in_flight("old")


def test_repeated_zero_steps_do_not_refresh_candidate_lru_age(case):
    helper, scheduler, blocks, rows = case
    for _ in range(20):
        helper.prepare(scheduler)
        assert helper.prepared
        helper.commit(0)
        ranks = helper.policy._free_queue_ranks(32)
        assert set(ranks) == {b.block_id for b in blocks}
        assert helper.pool.get_num_free_blocks() == 128
    helper.prepare(scheduler)
    assert len(helper.commit(1)) == 1


def test_zero_step_rollback_preserves_new_external_reference(case):
    helper, scheduler, blocks, rows = case
    helper.prepare(scheduler)
    helper.pool.touch([blocks[0]])  # A new request locally hits the prefix.
    helper.commit(0)
    assert blocks[0].ref_cnt == 1
    assert blocks[0].block_id not in helper.policy._free_queue_ranks(128)
    assert helper.pool.get_num_free_blocks() == 127
    helper.pool.free_blocks([blocks[0]])


def test_exception_rollback_never_releases_committed_pin(case):
    helper, scheduler, blocks, rows = case
    helper.prepare(scheduler)
    helper.commit(1)
    helper.rollback("exception_after_commit")
    assert all(b.ref_cnt == 1 for b in blocks) and helper.inflight


def test_cancel_reuse_and_delayed_failure_keep_original_batch(case):
    helper, scheduler, blocks, rows = case
    helper.prepare(scheduler)
    stores = helper.commit(1)
    helper.manager.on_request_reset("old")
    helper.manager.on_request_arrived("old")
    assert helper.manager._requests.in_flight_is_orphaned("old")
    for _ in range(3):
        helper.prepare(scheduler)
        assert not helper.prepared and all(b.ref_cnt == 1 for b in blocks)
    helper.manager.on_store_results({"old"}, {"old": 1})
    helper.receipts({"old": dict(describe(stores[0]), source="protection", batch_id="b")}, {"old"})
    assert "old" not in helper.policy._broken_prefixes
    assert helper.pool.get_num_free_blocks() == 128


def test_current_generation_store_failure_breaks_prefix(case):
    helper, scheduler, blocks, rows = case
    first = helper.policy._pending["old"][0].store_metadata
    suffix = replace(first, op=replace(first.op, token_ids=list(range(768)),
        start=512, end=768, block_ids=[list(range(33, 49))]))
    helper.manager.add_store_candidate(suffix)
    helper.prepare(scheduler)
    stores = helper.commit(1)
    output = NS(total_num_scheduled_tokens=512, scheduled_new_reqs=[],
        scheduled_cached_reqs=NS(new_block_ids=[]))
    # The default manager must not emit the suffix while our raw ID is in flight.
    assert helper.manager.on_scheduler_step(output).stores_to_submit == []
    assert len(helper.policy._pending["old"]) == 1
    helper.manager.on_store_results({"old"}, {"old": 1})
    helper.receipts({"old": dict(describe(stores[0]), source="protection", batch_id="b")}, {"old"})
    assert "old" not in helper.policy._pending
    assert helper.policy._counters.dropped_failed_store == 1
    assert all(b.ref_cnt == 0 for b in blocks)


def test_one_chunk_budget_accepts_one_complete_operation(case):
    helper, scheduler, blocks, rows = case
    meta = helper.policy._pending["old"][0].store_metadata
    helper.policy.drop_request("old")
    helper.manager.add_store_candidate(replace(meta, op=replace(meta.op,
        end=256, block_ids=[[b.block_id for b in blocks[:16]]])))
    helper.budget = 16
    helper.prepare(scheduler)
    assert len(helper.prepared["old"].blocks) == 16
    assert helper.pool.get_num_free_blocks() == 112
    helper.commit(0)
    assert helper.pool.get_num_free_blocks() == 128


def test_long_pending_operation_hands_off_bounded_prefix_and_retains_suffix(case):
    helper, scheduler, blocks, rows = case
    pending = helper.policy._pending["old"][0]
    ids = [b.block_id for b in blocks] + list(range(33, 65))
    op = replace(pending.store_metadata.op, token_ids=list(range(1024)),
                 end=1024, block_ids=[ids])
    pending.store_metadata = replace(pending.store_metadata, op=op)
    pending.block_hashes = {block_id: helper.pool.blocks[block_id].block_hash
                            for block_id in ids}

    helper.prepare(scheduler)
    prepared = helper.prepared["old"]
    assert len(prepared.blocks) == 32
    assert prepared.metadata.op.start == 0 and prepared.metadata.op.end == 512
    assert prepared.continuation is not None
    assert prepared.continuation.store_metadata.op.start == 512
    assert prepared.continuation.store_metadata.op.end == 1024

    stores = helper.commit(1)
    assert stores[0].op.start == 0 and stores[0].op.end == 512
    suffix = helper.policy._pending["old"]
    assert len(suffix) == 1 and suffix[0] is prepared.continuation
    assert helper.policy._counters.admitted == 1
    assert helper.policy._counters.emitted == 0
    assert next(row for row in rows if row["event"] == "protection_prepared")["acquired_pins"] == 32
    assert helper.manager.on_scheduler_step(NS(total_num_scheduled_tokens=512,
        scheduled_new_reqs=[], scheduled_cached_reqs=NS(new_block_ids=[]))).stores_to_submit == []

    helper.manager.on_store_results(set(), {"old": 1})
    helper.receipts({"old": dict(describe(stores[0]), source="protection", batch_id="b")}, set())
    assert helper.policy._pending["old"][0].store_metadata.op.start == 512
    # The native manager validates unchanged suffix hashes and pins only that
    # suffix. Force its danger window wide enough to exercise the real drain.
    helper.policy._blocks_per_step_ema = 128
    helper.policy._ema_initialized = True
    actions = helper.manager.on_scheduler_step(NS(total_num_scheduled_tokens=512,
        scheduled_new_reqs=[], scheduled_cached_reqs=NS(new_block_ids=[])))
    assert len(actions.stores_to_submit) == 1
    assert (actions.stores_to_submit[0].op.start, actions.stores_to_submit[0].op.end) == (512, 1024)
    assert not helper.policy._pending
    assert helper.policy._counters.admitted == helper.policy._counters.emitted == 1
    helper.manager.on_store_results(set(), {"old": 1})
    assert helper.pool.get_num_free_blocks() == 128


def test_long_pending_suffix_is_dropped_when_protected_prefix_fails(case):
    helper, scheduler, blocks, rows = case
    pending = helper.policy._pending["old"][0]
    ids = [b.block_id for b in blocks] + list(range(33, 65))
    pending.store_metadata = replace(pending.store_metadata, op=replace(
        pending.store_metadata.op, token_ids=list(range(1024)), end=1024, block_ids=[ids]))
    pending.block_hashes = {block_id: helper.pool.blocks[block_id].block_hash
                            for block_id in ids}
    helper.prepare(scheduler)
    stores = helper.commit(1)
    helper.manager.on_store_results({"old"}, {"old": 1})
    helper.receipts({"old": dict(describe(stores[0]), source="protection", batch_id="b")}, {"old"})
    assert "old" not in helper.policy._pending
    assert helper.policy._counters.dropped_failed_store == 1
    assert helper.policy._counters.admitted == helper.policy._counters.dropped_failed_store
    assert helper.policy._counters.emitted == 0


def test_inflight_budget_records_candidate_without_pinning(case):
    helper, scheduler, blocks, rows = case
    helper.inflight["other"] = NS(blocks=[NS(block_id=i) for i in range(33, 65)])
    before = helper.pool.get_num_free_blocks()
    helper.prepare(scheduler)
    helper.prepare(scheduler)
    assert not helper.prepared and helper.pool.get_num_free_blocks() == before
    assert all(b.ref_cnt == 0 for b in blocks)
    fallback = [r for r in rows if r["event"] == "protection_fallback"]
    assert len(fallback) == 1
    assert fallback[0]["reason"] == "inflight_budget"
    assert fallback[0]["required_blocks"] == 32
    assert fallback[0]["available_blocks"] == 0
    assert fallback[0]["inflight_blocks"] == 32
    assert fallback[0]["inflight_request_ids"] == ["other"]
    assert helper.policy._pending["old"]


def test_async_load_footprint_is_reserved_despite_zero_compute_and_lookup_status(case):
    helper, scheduler, blocks, rows = case
    scheduler.requests["new"].num_tokens_with_spec = 768
    scheduler.requests["new"].status = NS(name="WAITING_FOR_REMOTE_KVS")
    assert conservative_reserve(scheduler) == 2 * 49 + 16
    helper.prepare(scheduler)
    assert not helper.prepared  # compute_slots could be zero; full physical footprint cannot fit


def test_scheduler_wrapper_rolls_back_on_exception_before_metadata(case):
    helper, source, blocks, rows = case
    class Scheduler:
        def schedule(self):
            assert all(b.ref_cnt == 1 for b in blocks)
            raise RuntimeError("allocation failure")
    scheduler = Scheduler()
    scheduler.__dict__.update(source.__dict__)
    scheduler.connector = NS(_bounded=helper)
    install_protection_scheduler(Scheduler)
    install_protection_scheduler(Scheduler)
    with pytest.raises(RuntimeError, match="allocation failure"):
        scheduler.schedule()
    assert helper.pool.get_num_free_blocks() == 128 and not helper.prepared
