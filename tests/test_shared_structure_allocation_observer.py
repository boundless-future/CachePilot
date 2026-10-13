"""Real BlockPool checks; this isolates observation, not strategy admission."""
from types import SimpleNamespace as NS
import pytest
pytest.importorskip('lmcache')
from vllm.v1.core.block_pool import BlockPool
from scripts.shared_structure_allocation_connector import AllocationWindowProtection, WindowProtection


@pytest.fixture
def observed(monkeypatch):
    pool = BlockPool(33, enable_caching=False, hash_block_size=16)
    helper = object.__new__(AllocationWindowProtection)
    records = []
    helper.record = lambda event, **data: records.append(dict(event=event, **data))
    # Only native allocation observation is under test here; strategy and
    # connector callbacks have separate real service and admission gates.
    monkeypatch.setattr(WindowProtection, 'bind', lambda h, m: setattr(h, 'pool', m.block_pool))
    monkeypatch.setattr(WindowProtection, 'prepare', lambda h, s: None)
    helper.bind(NS(block_pool=pool))
    scheduler = NS(requests={}, max_num_scheduled_tokens=512,
                   kv_cache_manager=NS(watermark_blocks=0))
    return helper, scheduler, records


def test_zero_token_step_keeps_original_blocks_and_free_behavior(observed):
    h, s, records = observed
    h.prepare(s)
    blocks = h.pool.get_new_blocks(4)
    assert all(b is h.pool.blocks[b.block_id] and b.ref_cnt == 1 for b in blocks)
    h.pool.free_blocks(blocks)
    h.observe_completed_step(NS(total_num_scheduled_tokens=0))
    r = records[-1]
    assert r['allocated'] == 4 and r['scheduled_tokens'] == 0
    assert r['usable_blocks'] == r['free'] == r['free_after'] == 32
    assert r['calls'][0]['block_ids'] == [b.block_id for b in blocks]
    h.prepare(s)
    h.observe_completed_step(NS(total_num_scheduled_tokens=1))
    assert records[-1]['step'] == 2 and records[-1]['allocated'] == 0


def test_failed_allocation_propagates_and_is_not_counted(observed):
    h, s, records = observed
    h.prepare(s)
    with pytest.raises(ValueError):
        h.pool.get_new_blocks(33)
    h.observe_completed_step(NS(total_num_scheduled_tokens=0))
    assert records[-1]['calls'] == [] and records[-1]['free_after'] == 32


def test_boundary_must_be_settled_before_next_step(observed):
    h, s, _ = observed
    with pytest.raises(RuntimeError, match='outside observed'):
        h.pool.get_new_blocks(1)
    h.prepare(s)
    with pytest.raises(RuntimeError, match='not settled'):
        h.prepare(s)
    h.observe_completed_step(NS(total_num_scheduled_tokens=0))
    with pytest.raises(RuntimeError, match='Missing'):
        h.observe_completed_step(NS(total_num_scheduled_tokens=0))


def test_duplicate_observer_is_rejected(observed):
    h, _, _ = observed
    second = object.__new__(AllocationWindowProtection)
    with pytest.raises(RuntimeError, match='already installed'):
        second.bind(NS(block_pool=h.pool))
