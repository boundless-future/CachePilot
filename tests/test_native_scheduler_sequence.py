"""Run actual pinned Scheduler.schedule, without a worker or model weights.

Model config is real Qwen3-4B; KV metadata uses a small full-attention pool.
Synthetic external-hit callbacks are fixtures, not LMCache/GPU validation.
"""
from types import SimpleNamespace as NS
import pytest
pytest.importorskip('vllm')
import torch
from vllm import EngineArgs, SamplingParams
from vllm.v1.core.sched.scheduler import Scheduler
from vllm.v1.kv_cache_interface import FullAttentionSpec, KVCacheGroupSpec, KVCacheConfig
from vllm.v1.request import Request, RequestStatus
from vllm.v1.core import kv_cache_utils as utils
from vllm.utils.hashing import get_hash_fn_by_name


@pytest.fixture(scope='module')
def config():
    cfg=EngineArgs(model='/root/CachePilot/models/Qwen3-4B',max_model_len=512,
        max_num_batched_tokens=512,max_num_seqs=4,enforce_eager=True,
        enable_prefix_caching=True,async_scheduling=False).create_engine_config()
    cfg.cache_config.num_gpu_blocks=33
    return cfg


@pytest.fixture
def actual(config,monkeypatch):
    fn=get_hash_fn_by_name('sha256')
    monkeypatch.setattr(utils,'NONE_HASH',fn(utils.resolve_none_hash_seed(fn)),raising=False)
    kv=KVCacheConfig(num_blocks=33,kv_cache_tensors=[],kv_cache_groups=[KVCacheGroupSpec(
        ['layer'],FullAttentionSpec(block_size=16,num_kv_heads=1,head_size=8,dtype=torch.bfloat16))])
    sched=Scheduler(config,kv,NS(),block_size=16,hash_block_size=16)
    def add(rid,length,offset=0):
        r=Request(rid,list(range(offset,offset+length)),SamplingParams(max_tokens=2),None,
            block_hasher=utils.get_request_block_hasher(16,fn))
        sched.add_request(r);return r
    return sched,add


@pytest.mark.parametrize('extra_reference',[False,True])
@pytest.mark.parametrize('shared_prefix',[False,True])
def test_actual_prefill_sequence_accounts_for_same_step_prefix(actual,extra_reference,shared_prefix):
    sched,add=actual
    pool=sched.kv_cache_manager.block_pool
    pressure=pool.get_new_blocks(27)
    pin=pool.get_new_blocks(1) if extra_reference else []
    first=add('first',32);second=add('second',48,offset=0 if shared_prefix else 1000)
    output=sched.schedule()
    # Matching prefixes reuse the first request's already registered blocks
    # within this schedule; only 16 additional tokens are needed. Separate
    # unrelated cold inputs so this is not mistaken for partial cold admission.
    expected=({'first':32,'second':16} if shared_prefix else
              {'first':32} if extra_reference else {'first':32,'second':48})
    assert output.num_scheduled_tokens==expected
    assert first.status==RequestStatus.RUNNING
    assert second.status==(RequestStatus.WAITING if extra_reference and not shared_prefix
                          else RequestStatus.RUNNING)
    sched.finish_requests(['first','second'],RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pressure+pin)
    assert pool.get_num_free_blocks()==32 and not sched.requests


@pytest.mark.parametrize('extra_reference',[False,True])
def test_actual_async_zero_compute_step_keeps_native_reservations(actual,extra_reference):
    sched,add=actual
    # Only the connector callback is synthetic; scheduling, request states,
    # reserved-block calculation and allocation are real installed methods.
    allocated=[]
    sched.connector=NS(supports_divergent_local_hybrid_hits=False,
        on_new_request=lambda r:None,
        get_num_new_matched_tokens=lambda r,local:(r.num_prompt_tokens-1-local,True),
        update_state_after_alloc=lambda r,blocks,external:allocated.append((r.request_id,external)),
        build_connector_meta=lambda output:None,
        request_finished=lambda r,ids:(False,None))
    pool=sched.kv_cache_manager.block_pool
    pressure=pool.get_new_blocks(26)
    pin=pool.get_new_blocks(1) if extra_reference else []
    first=add('first',33);second=add('second',49)
    output=sched.schedule()
    assert output.total_num_scheduled_tokens==0
    assert first.status==RequestStatus.WAITING_FOR_REMOTE_KVS
    assert second.status==(RequestStatus.WAITING if extra_reference else RequestStatus.WAITING_FOR_REMOTE_KVS)
    assert allocated==([('first',32)] if extra_reference else [('first',32),('second',48)])
    assert sched._inflight_prefill_reserved_blocks()==(1 if extra_reference else 2)
    # Simulate completed remote transfers only for cleanup; no native DMA.
    sched.finished_recving_kv_req_ids.update(r.request_id for r in (first,second)
        if r.status==RequestStatus.WAITING_FOR_REMOTE_KVS)
    sched.finish_requests(['first','second'],RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pressure+pin)
    assert pool.get_num_free_blocks()==32 and not sched.requests
