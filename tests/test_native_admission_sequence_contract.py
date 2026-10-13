"""Real manager counterexamples: per-call slack is not whole-step slack."""
import pytest
pytest.importorskip('vllm')
from test_native_allocation_capacity_contract import native


@pytest.mark.parametrize('extra_reference', [False, True])
def test_same_first_admission_can_exclude_next_waiting_load(native, extra_reference):
    m, request, calls, demands=native
    # Leave five blocks: the original sequence needs two, then three.
    pressure=m.block_pool.get_new_blocks(27)
    pinned=[]
    if extra_reference:
        pinned=m.block_pool.get_new_blocks(1)
    calls.clear(); demands.clear()
    first=request('first')
    second=request('second')
    assert m.allocate_slots(first,0,num_external_computed_tokens=32,delay_cache_blocks=True) is not None
    next_result=m.allocate_slots(second,0,num_external_computed_tokens=48,delay_cache_blocks=True)
    assert (next_result is None) == extra_reference
    assert demands==[2,3]
    assert sum(map(len,calls)) == (2 if extra_reference else 5)
    m.free(first)
    if next_result is not None:
        m.free(second)
    m.block_pool.free_blocks(pressure+pinned)
    assert m.block_pool.get_num_free_blocks()==32


def test_pin_on_the_same_local_hit_changes_native_demand(native):
    m,request,calls,demands=native
    seed=request('seed');m.allocate_slots(seed,64);m.free(seed)
    target=request('target');hits,tokens,_=m.get_computed_blocks(target)
    block=hits.blocks[0][0]
    before=m.block_pool.get_num_free_blocks()
    # Extra owner of a block that native admission was about to touch.
    m.block_pool.touch([block])
    calls.clear();demands.clear()
    assert m.allocate_slots(target,1,num_new_computed_tokens=tokens,new_computed_blocks=hits) is not None
    assert demands==[4]  # baseline single-call fixture required five.
    assert before-m.block_pool.get_num_free_blocks()==5  # not five plus one.
    assert block.ref_cnt==2
    m.free(target)
    assert block.ref_cnt==1
    m.block_pool.free_blocks([block])
    assert m.block_pool.get_num_free_blocks()==32


def test_post_allocation_rollback_cannot_recover_evicted_hash(native):
    m,request,calls,demands=native
    seed=request('seed');m.allocate_slots(seed,64);m.free(seed)
    target=request('target');hits,tokens,_=m.get_computed_blocks(target)
    assert tokens==64
    original_hashes=[b.block_hash for b in hits.blocks[0]]
    pinned=hits.blocks[0][0]
    m.block_pool.touch([pinned])
    # A subsequent allocation consumes all remaining free blocks and evicts
    # three cached prefix blocks. Unpin restores capacity, not old contents.
    allocated=m.block_pool.get_new_blocks(m.block_pool.get_num_free_blocks())
    m.block_pool.free_blocks([pinned])
    m.block_pool.free_blocks(allocated)
    assert m.block_pool.get_num_free_blocks()==32
    assert original_hashes[0]==pinned.block_hash
    assert all(b.block_hash is None for b in hits.blocks[0][1:])
    _,new_tokens,_=m.get_computed_blocks(target)
    assert new_tokens==16
