"""Characterize pinned LMCache's passive pending STORE lifetime, without CUDA."""
from types import SimpleNamespace as NS

import pytest

pytest.importorskip('lmcache')
from lmcache.integration.vllm.lazy_offload_manager import LazyOffloadManager


@pytest.mark.parametrize('deadline', [0, 0.001])
def test_finished_unsubmitted_store_does_not_end_on_zero_token_steps(deadline):
    manager = LazyOffloadManager({
        'lmcache.mp.lazy_offload_policy': 'EVICTION_AWARE',
        'lmcache.mp.lazy_offload_max_deferral_seconds': deadline}, [16], NS())
    manager.bind_block_pool(NS())
    manager.on_request_arrived('r')
    metadata = NS(request_id='r', op=NS(start=0, end=256))
    manager._policy.add(metadata, {1: 'original-hash'})
    # The deadline has already elapsed; idle steps still must not submit a
    # STORE which vLLM's no-forward worker path would discard.
    manager._policy._pending['r'][0].admitted_at_time -= 60
    assert not manager.on_request_finished('r').sessions_to_end
    for _ in range(100):
        actions = manager.on_scheduler_step(NS(total_num_scheduled_tokens=0))
        assert not actions.stores_to_submit and not actions.sessions_to_end
    assert manager._requests.is_finished('r')
    assert manager._policy.has_pending_request('r')
    assert not manager._requests.has_in_flight('r')
    assert not manager.has_inflight_store_work()
