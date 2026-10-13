"""The pinned Scheduler computes a later-prefill reservation, not just one call."""
import hashlib
import inspect
from pathlib import Path
import pytest
pytest.importorskip('vllm')
from vllm.v1.core.sched.scheduler import Scheduler
from test_native_allocation_capacity_contract import native


def scheduler_with_manager(manager):
    assert hashlib.sha256(Path(inspect.getsourcefile(Scheduler)).read_bytes()).hexdigest() == (
        'c1db45f3bbad3a875dd8638331b8870c4380ac1ab4734265ba5c863833a82884')
    scheduler=object.__new__(Scheduler)
    scheduler.kv_cache_manager=manager
    scheduler.max_model_len=512
    scheduler._inflight_prefills=set()
    return scheduler


def test_async_load_cannot_consume_other_prefill_reservation(native):
    m,request,calls,demands=native
    sched=scheduler_with_manager(m)
    first=request('inflight')
    assert m.allocate_slots(first,32) is not None
    first.num_computed_tokens=32
    sched._inflight_prefills.add(first)
    assert sched._request_remaining_blocks(first)==3
    assert sched._inflight_prefill_reserved_blocks()==3
    pressure=m.block_pool.get_new_blocks(24)
    assert m.block_pool.get_num_free_blocks()==6
    second=request('async')
    before=m.block_pool.get_num_free_blocks()
    assert m.allocate_slots(second,0,num_external_computed_tokens=64,
        delay_cache_blocks=True,reserved_blocks=sched._inflight_prefill_reserved_blocks()) is None
    assert m.block_pool.get_num_free_blocks()==before
    assert m.allocate_slots(second,0,num_external_computed_tokens=64,delay_cache_blocks=True,
        reserved_blocks=0) is not None
    assert m.block_pool.get_num_free_blocks()==2
    assert m.allocate_slots(first,33) is None  # original in-flight request cannot finish
    m.free(second)
    m.block_pool.free_blocks(pressure)
    assert m.allocate_slots(first,33) is not None
    m.free(first)
    assert m.block_pool.get_num_free_blocks()==32
