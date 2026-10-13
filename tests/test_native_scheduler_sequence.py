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
from cachepilot.runtime.whole_step import WholeStepAdmission, whole_step_envelope


@pytest.fixture(scope='module')
def config():
    cfg=EngineArgs(model='/root/CachePilot/models/Qwen3-4B',max_model_len=512,
        max_num_batched_tokens=512,max_num_seqs=4,enforce_eager=True,
        enable_prefix_caching=True,async_scheduling=False).create_engine_config()
    cfg.cache_config.num_gpu_blocks=33
    return cfg


@pytest.fixture
def actual(config,monkeypatch,request):
    fn=get_hash_fn_by_name('sha256')
    monkeypatch.setattr(utils,'NONE_HASH',fn(utils.resolve_none_hash_seed(fn)),raising=False)
    kv=KVCacheConfig(num_blocks=getattr(request,'param',33),kv_cache_tensors=[],kv_cache_groups=[KVCacheGroupSpec(
        ['layer'],FullAttentionSpec(block_size=16,num_kv_heads=1,head_size=8,dtype=torch.bfloat16))])
    sched=Scheduler(config,kv,NS(),block_size=16,hash_block_size=16)
    def add(rid,length,offset=0):
        r=Request(rid,list(range(offset,offset+length)),SamplingParams(max_tokens=2),None,
            block_hasher=utils.get_request_block_hasher(16,fn))
        sched.add_request(r);return r
    return sched,add


@pytest.mark.parametrize('actual',[129],indirect=True)
@pytest.mark.parametrize('owner_leaves',[False,True])
@pytest.mark.parametrize('external',[False,True])
@pytest.mark.parametrize('pin_count',[0,16,32])
def test_envelope_shared_owner_and_watermark_sequence(actual,owner_leaves,external,pin_count):
    sched,add=actual
    manager=sched.kv_cache_manager
    manager.watermark_blocks=7
    pool=manager.block_pool
    pressure=pool.get_new_blocks(48)
    seed=add('seed',32)
    assert manager.allocate_slots(seed,32) is not None
    sched.waiting.remove(seed)
    del sched.requests['seed']
    if owner_leaves:manager.free(seed)
    matched=[]
    if external:
        sched.connector=NS(supports_divergent_local_hybrid_hits=False,
            on_new_request=lambda r:None,
            get_num_new_matched_tokens=lambda r,local:(r.num_prompt_tokens-1-local,True),
            update_state_after_alloc=lambda r,blocks,ext:matched.append((r.request_id,ext)),
            build_connector_meta=lambda output:None,request_finished=lambda r,ids:(False,None))
    first=add('first',65)
    second=add('second',49,1000)
    e=whole_step_envelope(sched)
    assert e.request_blocks==(('first',5),('second',4)) and e.watermark_blocks==7
    # Exercise equality, not only a roomy pool. One further owned reference
    # must be rejected without changing the native allocator state.
    pressure.extend(pool.get_new_blocks(pool.get_num_free_blocks()-pin_count-e.reserve))
    free=pool.get_num_free_blocks()
    assert free-pin_count==e.reserve
    assert not WholeStepAdmission().permits(free,pin_count+1,e.reserve)
    assert WholeStepAdmission().permits(pool.get_num_free_blocks(),pin_count,e.reserve)
    pin=pool.get_new_blocks(pin_count) if pin_count else []
    output=sched.schedule()
    if external:
        assert output.total_num_scheduled_tokens==0
        assert matched==[('first',32),('second',48)]
        assert first.status==second.status==RequestStatus.WAITING_FOR_REMOTE_KVS
        assert sched._inflight_prefill_reserved_blocks()==2
        sched.finished_recving_kv_req_ids.update(['first','second'])
    else:
        assert output.num_scheduled_tokens=={'first':33,'second':49}
        assert first.status==second.status==RequestStatus.RUNNING
    sched.finish_requests(['first','second'],RequestStatus.FINISHED_ABORTED)
    if not owner_leaves:manager.free(seed)
    pool.free_blocks(pressure+pin)
    assert pool.get_num_free_blocks()==128 and not sched.requests


@pytest.mark.parametrize('actual',[129],indirect=True)
@pytest.mark.parametrize('pin_count',[0,16,32])
def test_envelope_native_preemption_reentry_repeated(actual,pin_count):
    import time
    from vllm.v1.outputs import ModelRunnerOutput
    sched,add=actual
    manager=sched.kv_cache_manager
    manager.watermark_blocks=7
    pool=manager.block_pool
    pressure=pool.get_new_blocks(48)
    first=add('first',65)
    pending=sched.schedule()
    assert pending.num_scheduled_tokens=={'first':65}
    for iteration in range(3):
        sched.running.remove(first)
        sched._preempt_request(first,time.monotonic())
        assert first.status==RequestStatus.PREEMPTED and first.num_computed_tokens==0
        assert first.num_preemptions==iteration+1
        second=add(f'second-{iteration}',49,1000+iteration*100)
        # The real hook preserves outstanding worker tokens. The candidate
        # must refuse estimation until the stale result is actually drained.
        assert first.num_in_flight_tokens>0
        assert WholeStepAdmission().reserve(sched) is None
        ids=list(pending.num_scheduled_tokens)
        sched.update_from_output(pending,ModelRunnerOutput(
            req_ids=ids,req_id_to_index={rid:i for i,rid in enumerate(ids)}))
        assert first.num_in_flight_tokens==first.num_stale_output_tokens==0
        reserve=WholeStepAdmission().reserve(sched)
        assert reserve is not None
        assert WholeStepAdmission().permits(pool.get_num_free_blocks(),pin_count,reserve)
        pin=pool.get_new_blocks(pin_count) if pin_count else []
        output=sched.schedule()
        # Native PREEMPTED path resumes; cached prefix and status choices are
        # decided by the installed scheduler, not an emulated allocation loop.
        assert set(output.num_scheduled_tokens)=={'first',second.request_id}
        assert first.status==second.status==RequestStatus.RUNNING
        pending=output
        sched.finish_requests(second.request_id,RequestStatus.FINISHED_ABORTED)
        pool.free_blocks(pin)
    sched.finish_requests('first',RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pressure)
    assert pool.get_num_free_blocks()==128 and not sched.requests


@pytest.mark.parametrize('actual',[97],indirect=True)
@pytest.mark.parametrize('pin_count',[0,16,32])
def test_envelope_natural_decode_preemption_and_reentry(actual,pin_count):
    from vllm.v1.outputs import ModelRunnerOutput
    sched,add=actual
    sched.structured_output_manager=NS(accept_tokens=lambda request,tokens:True)
    pool=sched.kv_cache_manager.block_pool
    pressure=pool.get_new_blocks(92)
    first=add('first',64)
    prefill=sched.schedule()
    assert prefill.num_scheduled_tokens=={'first':64}
    sched.update_from_output(prefill,ModelRunnerOutput(
        req_ids=['first'],req_id_to_index={'first':0},sampled_token_ids=[[3000]]))
    assert first.num_in_flight_tokens==0 and first.num_tokens==65
    assert pool.get_num_free_blocks()==0
    # The next decode token needs a fifth block. Let schedule itself select
    # the preemption; no direct request-state or running-list mutation.
    failed=sched.schedule()
    assert failed.num_scheduled_tokens=={}
    assert first.status==RequestStatus.PREEMPTED and first.num_preemptions==1
    assert first.num_computed_tokens==first.num_in_flight_tokens==0
    reserve=WholeStepAdmission().reserve(sched)
    assert reserve==21
    assert not WholeStepAdmission().permits(pool.get_num_free_blocks(),pin_count,reserve)
    # Repeated insufficient-capacity attempts must remain non-admissions.
    for _ in range(3):
        assert sched.schedule().num_scheduled_tokens=={}
        assert first.status==RequestStatus.PREEMPTED
        assert WholeStepAdmission().reserve(sched)==reserve
    pool.free_blocks(pressure)
    assert WholeStepAdmission().permits(pool.get_num_free_blocks(),pin_count,reserve)
    pin=pool.get_new_blocks(pin_count) if pin_count else []
    resumed=sched.schedule()
    assert resumed.num_scheduled_tokens=={'first':1}
    assert first.status==RequestStatus.RUNNING
    sched.finish_requests('first',RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pin)
    assert pool.get_num_free_blocks()==96 and not sched.requests


@pytest.mark.parametrize('actual',[97],indirect=True)
@pytest.mark.parametrize('remote',[False,True])
@pytest.mark.parametrize('pin_count',[0,16,32])
def test_whole_step_envelope_allows_complete_native_sequence(actual,remote,pin_count):
    from cachepilot.runtime.whole_step import WholeStepAdmission
    sched,add=actual
    if remote:
        sched.connector=NS(supports_divergent_local_hybrid_hits=False,
            on_new_request=lambda r:None,
            get_num_new_matched_tokens=lambda r,local:(r.num_prompt_tokens-1-local,True),
            update_state_after_alloc=lambda *a:None,build_connector_meta=lambda output:None,
            request_finished=lambda r,ids:(False,None))
    first=add('first',33);second=add('second',49,1000)
    pool=sched.kv_cache_manager.block_pool
    pressure=pool.get_new_blocks(32)
    policy=WholeStepAdmission()
    reserve=policy.reserve(sched)
    assert reserve is not None and policy.permits(pool.get_num_free_blocks(),pin_count,reserve)
    pin=pool.get_new_blocks(pin_count) if pin_count else []
    output=sched.schedule()
    if remote:
        assert output.total_num_scheduled_tokens==0
        assert all(r.status==RequestStatus.WAITING_FOR_REMOTE_KVS for r in (first,second))
        assert sched._inflight_prefill_reserved_blocks()==2
        sched.finished_recving_kv_req_ids.update(['first','second'])
    else:
        assert output.num_scheduled_tokens=={'first':33,'second':49}
    sched.finish_requests(['first','second'],RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pressure+pin)
    assert pool.get_num_free_blocks()==96 and not sched.requests


@pytest.mark.parametrize('actual',[97],indirect=True)
def test_whole_step_existing_remote_reservation_and_later_arrival(actual):
    from cachepilot.runtime.whole_step import whole_step_envelope
    sched,add=actual
    sched.connector=NS(supports_divergent_local_hybrid_hits=False,
        on_new_request=lambda r:None,
        get_num_new_matched_tokens=lambda r,local:(r.num_prompt_tokens-1-local,True),
        update_state_after_alloc=lambda *a:None,build_connector_meta=lambda output:None,
        request_finished=lambda r,ids:(False,None))
    pool=sched.kv_cache_manager.block_pool
    pressure=pool.get_new_blocks(32)
    first=add('first',33)
    assert sched.schedule().total_num_scheduled_tokens==0
    assert first.status==RequestStatus.WAITING_FOR_REMOTE_KVS
    second=add('second',49,1000)
    envelope=whole_step_envelope(sched)
    assert envelope.remote_blocks==3 and envelope.reserve==26
    assert pool.get_num_free_blocks()-32>=envelope.reserve
    pin=pool.get_new_blocks(32)
    assert sched.schedule().total_num_scheduled_tokens==0
    assert second.status==RequestStatus.WAITING_FOR_REMOTE_KVS
    assert sched._inflight_prefill_reserved_blocks()==2
    sched.finished_recving_kv_req_ids.update(['first','second'])
    sched.finish_requests(['first','second'],RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pressure+pin)
    assert pool.get_num_free_blocks()==96 and not sched.requests


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


@pytest.mark.parametrize('owner_leaves',[False,True])
def test_actual_shared_owner_departure_changes_free_touch_demand(actual,owner_leaves):
    sched,add=actual
    manager=sched.kv_cache_manager
    pool=manager.block_pool
    pressure=pool.get_new_blocks(27)
    seed=add('seed',32)
    assert manager.allocate_slots(seed,32) is not None
    # Remove the fixture owner from scheduling, keeping or releasing its refs.
    sched.waiting.remove(seed)
    del sched.requests['seed']
    if owner_leaves:
        manager.free(seed)
    target=add('target',48)
    output=sched.schedule()
    assert output.num_scheduled_tokens=={'target':16}
    assert target.status==RequestStatus.RUNNING
    assert pool.get_num_free_blocks()==2
    sched.finish_requests('target',RequestStatus.FINISHED_ABORTED)
    if not owner_leaves:
        manager.free(seed)
    pool.free_blocks(pressure)
    assert pool.get_num_free_blocks()==32 and not sched.requests


@pytest.mark.parametrize('extra_reference',[False,True])
def test_actual_full_sequence_gate_rejects_small_next_chunk(actual,extra_reference):
    sched,add=actual
    sched.max_num_scheduled_tokens=16
    pool=sched.kv_cache_manager.block_pool
    pressure=pool.get_new_blocks(30)
    pin=pool.get_new_blocks(1) if extra_reference else []
    target=add('target',65)
    output=sched.schedule()
    # One next-chunk block fits in both cases, but the full cold sequence does not.
    assert output.num_scheduled_tokens=={} and target.status==RequestStatus.WAITING
    assert pool.get_num_free_blocks()==(1 if extra_reference else 2)
    sched.finish_requests('target',RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pressure+pin)
    assert pool.get_num_free_blocks()==32 and not sched.requests


def test_actual_mixed_local_external_admission_retains_native_reservation(actual):
    sched,add=actual
    manager=sched.kv_cache_manager
    pool=manager.block_pool
    pressure=pool.get_new_blocks(26)
    seed=add('seed',32)
    assert manager.allocate_slots(seed,32) is not None
    sched.waiting.remove(seed)
    del sched.requests['seed']
    manager.free(seed)
    allocated=[]
    sched.connector=NS(supports_divergent_local_hybrid_hits=False,
        on_new_request=lambda r:None,
        get_num_new_matched_tokens=lambda r,local:(r.num_prompt_tokens-1-local,True),
        update_state_after_alloc=lambda r,blocks,external:allocated.append((r.request_id,external)),
        build_connector_meta=lambda output:None,request_finished=lambda r,ids:(False,None))
    target=add('target',65)
    output=sched.schedule()
    assert output.total_num_scheduled_tokens==0
    assert target.status==RequestStatus.WAITING_FOR_REMOTE_KVS
    assert allocated==[('target',32)]
    assert pool.get_num_free_blocks()==2
    assert sched._inflight_prefill_reserved_blocks()==1
    sched.finished_recving_kv_req_ids.add('target')
    sched.finish_requests('target',RequestStatus.FINISHED_ABORTED)
    pool.free_blocks(pressure)
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
