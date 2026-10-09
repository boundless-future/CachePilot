"""Real BlockPool/LMCache manager contracts; no model server or CUDA needed."""
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
from scripts.bounded_protection import BoundedProtection, conservative_reserve, install_protection_scheduler
from scripts.protection_handoff import describe
from scripts.owned_service_protocol import CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG
from scripts.token_prefix_proof import build_proof


@pytest.fixture
def case(monkeypatch):
    hash_fn = get_hash_fn_by_name("sha256")
    monkeypatch.setattr(utils, "NONE_HASH", hash_fn(utils.resolve_none_hash_seed(hash_fn)), raising=False)
    pool = BlockPool(129, enable_caching=True, hash_block_size=16)
    manager = LazyOffloadManager({"lmcache.mp.lazy_offload_policy": "EVICTION_AWARE"},
        [16], NS(update_pending_store_count=lambda rid, count: count == 1))
    manager.bind_block_pool(pool)
    helper = object.__new__(BoundedProtection)
    helper.pool, helper.manager, helper.policy = pool, manager, manager._policy
    helper.hash_fn, helper.hasher, helper.budget = hash_fn, TokenHasher(256, "blake3"), 32
    helper.prepared, helper.inflight, helper.committed_this_step = {}, {}, set()
    rows = []
    helper.record = lambda event, **kw: rows.append(dict(event=event, **kw))
    tokens = list(range(512))
    proof = build_proof(tokens, vllm_hash_function=hash_fn, vllm_initial_hash=utils.NONE_HASH,
                        lmcache_hasher=helper.hasher)
    blocks = pool.get_new_blocks(32)
    from vllm import SamplingParams
    from vllm.v1.request import Request
    request = Request("old", tokens, SamplingParams(max_tokens=1), None,
        block_hasher=utils.get_request_block_hasher(16, hash_fn))
    pool.cache_full_blocks(request, blocks, 0, 32, 16, 0)
    # Put the candidate at the queue head, matching actual eviction pressure.
    rest = pool.get_new_blocks(pool.get_num_free_blocks())
    filler = Request("filler", list(range(2000, 2000 + len(rest) * 16)), SamplingParams(max_tokens=1), None,
        block_hasher=utils.get_request_block_hasher(16, hash_fn))
    pool.cache_full_blocks(filler, rest, 0, len(rest), 16, 0)
    pool.free_blocks(blocks)
    pool.free_blocks(rest)
    meta = LMCacheMPRequestMetadata("old", "STORE", LoadStoreOp(tokens,
        [[b.block_id for b in blocks]], 0, 512), cache_salt="",
        request_configs={CLIENT_CONFIG: 1, SEQUENCE_CONFIG: 1, GENERATION_CONFIG: "a"*32})
    manager._requests.arrive("old")
    manager.add_store_candidate(meta)
    manager.on_request_finished("old")
    scheduler = NS(requests={"new": NS(num_tokens_with_spec=256, num_computed_tokens=0)},
        max_num_scheduled_tokens=512, kv_cache_manager=NS(watermark_blocks=0))
    return helper, scheduler, blocks, rows


def test_pin_precedes_allocator_and_original_receipt_releases_once(case):
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


@pytest.mark.parametrize("mutation", ["hash", "tokens", "referenced", "budget", "capacity", "active_id", "inflight", "partial"])
def test_invalid_or_unsupported_candidate_is_never_pinned(case, mutation):
    helper, scheduler, blocks, rows = case
    if mutation == "hash":
        blocks[0].reset_hash()
    elif mutation == "tokens":
        helper.policy._pending["old"][0].store_metadata.op.token_ids[0] = 9876
    elif mutation == "referenced":
        helper.pool.touch([blocks[0]])
    elif mutation == "budget":
        helper.budget = 16
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
    if mutation == "budget":
        fallback = next(r for r in rows if r["event"] == "protection_fallback")
        assert fallback["reason"] == "candidate_exceeds_budget"
        assert fallback["required_blocks"] == 32 and fallback["available_blocks"] == 16
        assert fallback["acquired_pins"] == 0
        assert helper.policy._pending["old"] and not helper.manager._requests.has_in_flight("old")


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
