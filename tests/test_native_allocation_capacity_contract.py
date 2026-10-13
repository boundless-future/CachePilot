"""Pinned vLLM capacity semantics, without model execution or a new reserve.

These are real KVCacheManager/BlockPool operations. They characterize one
allocation call; they do not bound a future scheduler step or protect any KV.
"""
import hashlib
import inspect
from pathlib import Path
import pytest
pytest.importorskip('vllm')
import torch
from vllm import SamplingParams
from vllm.v1.request import Request
from vllm.v1.core.kv_cache_manager import KVCacheManager
from vllm.v1.core import kv_cache_utils as utils
from vllm.v1.kv_cache_interface import FullAttentionSpec, KVCacheGroupSpec, KVCacheConfig
from vllm.utils.hashing import get_hash_fn_by_name


@pytest.fixture
def native(monkeypatch):
    assert hashlib.sha256(Path(inspect.getsourcefile(KVCacheManager)).read_bytes()).hexdigest() == (
        '58800535eda854580d84e1025bf32fe01b2a43ce0bba501b4f3d125e4f2c8afa')
    hash_fn = get_hash_fn_by_name('sha256')
    monkeypatch.setattr(utils, 'NONE_HASH', hash_fn(utils.resolve_none_hash_seed(hash_fn)), raising=False)
    spec = FullAttentionSpec(block_size=16, num_kv_heads=1, head_size=8, dtype=torch.bfloat16)
    config = KVCacheConfig(num_blocks=33, kv_cache_tensors=[],
                          kv_cache_groups=[KVCacheGroupSpec(['layer'], spec)])
    manager = KVCacheManager(config, max_model_len=512, scheduler_block_size=16, hash_block_size=16)
    calls, demands = [], []
    allocate = manager.block_pool.get_new_blocks
    count = manager.coordinator.get_num_blocks_to_allocate

    def observe(*args, **kwargs):
        blocks = allocate(*args, **kwargs)
        calls.append([b.block_id for b in blocks])
        return blocks

    def demand(*args, **kwargs):
        result = count(*args, **kwargs)
        demands.append(result)
        return result

    monkeypatch.setattr(manager.block_pool, 'get_new_blocks', observe)
    monkeypatch.setattr(manager.coordinator, 'get_num_blocks_to_allocate', demand)

    def request(rid, tokens=65):
        return Request(rid, list(range(tokens)), SamplingParams(max_tokens=2), None,
                       block_hasher=utils.get_request_block_hasher(16, hash_fn))

    return manager, request, calls, demands


def test_async_remote_load_allocates_without_compute(native):
    m, request, calls, demands = native
    r = request('async')
    result = m.allocate_slots(r, 0, num_external_computed_tokens=64, delay_cache_blocks=True)
    assert result is not None and demands == [4]
    assert sum(map(len, calls)) == 4 and m.block_pool.get_num_free_blocks() == 28
    m.free(r)
    assert m.block_pool.get_num_free_blocks() == 32


@pytest.mark.parametrize('existing_owner', [False, True])
def test_local_hit_capacity_counts_only_zero_ref_touches(native, existing_owner):
    m, request, calls, demands = native
    seed = request('seed')
    assert m.allocate_slots(seed, 64) is not None
    if not existing_owner:
        m.free(seed)
    target = request('target')
    hits, tokens, shared_boundary = m.get_computed_blocks(target)
    assert shared_boundary == 0
    assert tokens == 64
    calls.clear(); demands.clear()
    free_before = m.block_pool.get_num_free_blocks()
    assert m.allocate_slots(target, 1, num_new_computed_tokens=tokens, new_computed_blocks=hits) is not None
    expected = 1 if existing_owner else 5
    assert demands == [expected]
    assert sum(map(len, calls)) == 1
    assert free_before-m.block_pool.get_num_free_blocks() == expected
    m.free(target)
    if existing_owner:
        m.free(seed)
    assert m.block_pool.get_num_free_blocks() == 32


def test_mixed_local_and_external_zero_compute(native):
    m, request, calls, demands = native
    seed = request('seed')
    m.allocate_slots(seed, 32)
    m.free(seed)
    target = request('mixed')
    hits, tokens, shared_boundary = m.get_computed_blocks(target)
    assert shared_boundary == 0
    assert tokens == 32
    calls.clear(); demands.clear()
    before = m.block_pool.get_num_free_blocks()
    assert m.allocate_slots(target, 0, num_new_computed_tokens=tokens, new_computed_blocks=hits,
        num_external_computed_tokens=32, delay_cache_blocks=True) is not None
    assert demands == [4] and sum(map(len, calls)) == 2
    assert before-m.block_pool.get_num_free_blocks() == 4
    m.free(target)
    assert m.block_pool.get_num_free_blocks() == 32


@pytest.mark.parametrize('boundary', ['reservation', 'watermark'])
def test_native_headroom_rejects_before_physical_allocation(native, boundary):
    m, request, calls, demands = native
    kwargs = dict(num_external_computed_tokens=64, delay_cache_blocks=True)
    if boundary == 'reservation':
        kwargs['reserved_blocks'] = 29
    else:
        m.watermark_blocks = 29
        kwargs['has_scheduled_reqs'] = True
    assert m.allocate_slots(request('rejected'), 0, **kwargs) is None
    assert demands == [4] and calls == []
    assert m.block_pool.get_num_free_blocks() == 32


def test_running_increment_uses_existing_request_blocks(native):
    m, request, calls, demands = native
    r = request('running')
    m.allocate_slots(r, 16)
    r.num_computed_tokens = 16
    calls.clear(); demands.clear()
    assert m.allocate_slots(r, 1) is not None
    assert demands == [1] and sum(map(len, calls)) == 1
    m.free(r)
    assert m.block_pool.get_num_free_blocks() == 32


def test_full_sequence_gate_is_distinct_from_next_chunk_capacity(native):
    m, request, calls, demands = native
    pressure = m.block_pool.get_new_blocks(30)
    calls.clear(); demands.clear()
    r = request('chunk')
    assert m.allocate_slots(r, 16, full_sequence_must_fit=True) is None
    assert demands == [5] and calls == []
    assert m.allocate_slots(r, 16, full_sequence_must_fit=False) is not None
    assert demands == [5, 1] and sum(map(len, calls)) == 1
    m.free(r); m.block_pool.free_blocks(pressure)
    assert m.block_pool.get_num_free_blocks() == 32
