"""Real pinned-stack fixtures; import only from upstream contract tests."""
from types import SimpleNamespace as NS
import pytest
pytest.importorskip("lmcache")
pytest.importorskip("vllm")
from lmcache.integration.vllm.lazy_offload_manager import LazyOffloadManager
from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPRequestMetadata, LoadStoreOp
from lmcache.v1.multiprocess.token_hasher import TokenHasher
from vllm.v1.core import kv_cache_utils as utils
from vllm.v1.core.block_pool import BlockPool
from vllm.utils.hashing import get_hash_fn_by_name
from cachepilot.runtime.protection import BoundedProtection
from cachepilot.runtime.prefix_proof import build_proof
from cachepilot.integration.lmcache.protocol import CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG, wire_id
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


def pool_state(pool):
    return (pool.get_num_free_blocks(),
            [(b.ref_cnt,b.block_hash) for b in pool.blocks],
            [b.block_id for b in pool.free_block_queue.get_all_free_blocks()])
