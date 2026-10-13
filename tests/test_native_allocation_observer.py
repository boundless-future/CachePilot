"""Native per-call instrumentation retains manager results and errors."""
import pytest
pytest.importorskip('vllm')
from test_native_allocation_capacity_contract import native
from scripts.native_allocation_observer import install_native_allocation_observer


def observe(manager):
    records=[]
    install_native_allocation_observer(manager, lambda event, **data: records.append(dict(event=event, **data)),
        lambda: dict(step=1, full_reserve=100))
    return records


def test_remote_call_and_rejected_call_retain_results(native):
    m, request, _, _ = native
    records=observe(m)
    r=request('async')
    assert m.allocate_slots(r, 0, num_external_computed_tokens=64, delay_cache_blocks=True) is not None
    first=records[0]
    assert first['new_blocks']==4 and first['touched_free_blocks']==0
    assert first['demands']==[dict(required=4, free=32)] and first['num_new_tokens']==0
    assert m.allocate_slots(request('rejected'), 0, num_external_computed_tokens=64,
        delay_cache_blocks=True, reserved_blocks=25) is None
    assert records[-1]['serial']==2 and not records[-1]['success']
    assert records[-1]['demands']==[dict(required=4,free=28)] and records[-1]['new_blocks']==0
    m.free(r)
    assert m.block_pool.get_num_free_blocks()==32


def test_gpu_hit_touch_and_new_allocation_are_separate(native):
    m, request, _, _=native
    seed=request('seed'); m.allocate_slots(seed,64); m.free(seed)
    target=request('hit'); hits,tokens,_=m.get_computed_blocks(target)
    records=observe(m)
    assert m.allocate_slots(target,1,num_new_computed_tokens=tokens,new_computed_blocks=hits) is not None
    r=records[0]
    assert r['new_blocks']==1 and r['touched_free_blocks']==4
    assert r['free_before']-r['free_after']==5 and r['demands']==[dict(required=5,free=32)]
    m.free(target)
    assert m.block_pool.get_num_free_blocks()==32


def test_outside_call_demand_is_not_misattributed(native):
    m, request, _, _=native
    records=observe(m)
    m.coordinator.get_num_blocks_to_allocate(request_id='outside',num_tokens=16,
        new_computed_blocks=m.empty_kv_cache_blocks.blocks,num_encoder_tokens=0,
        total_computed_tokens=0,num_local_computed_tokens=0,num_tokens_main_model=16)
    assert not records
    with pytest.raises(ValueError):
        m.allocate_slots(request('bad'),0)
    assert records[-1]['error'] is not None and not records[-1]['success']
    assert m.allocate_slots(request('after'),1) is not None
    assert records[-1]['serial']==2 and records[-1]['success']


def test_second_installer_is_rejected(native):
    m,*_=native
    observe(m)
    with pytest.raises(RuntimeError,match='already installed'):
        observe(m)
