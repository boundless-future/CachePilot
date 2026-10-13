"""Pinned KVCacheManager fixture shared by native scheduler contracts."""
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
