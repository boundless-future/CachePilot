"""Decision contracts run without the serving stack or block mutations."""
from types import SimpleNamespace as NS
import pytest
from cachepilot.runtime.admission import ConservativeAdmission
from cachepilot.runtime.candidates import ranked_candidates


def test_reserve_includes_remote_and_computed_footprints():
    scheduler = NS(requests={'remote': NS(num_tokens_with_spec=64, num_computed_tokens=0),
                             'running': NS(num_tokens_with_spec=16, num_computed_tokens=48)},
                   max_num_scheduled_tokens=512, kv_cache_manager=NS(watermark_blocks=3))
    assert ConservativeAdmission().reserve(scheduler) == 2 * (5 + 4) + 3 + 16
    scheduler.requests['remote'].mm_features = ['image']
    assert ConservativeAdmission().reserve(scheduler) is None
    scheduler.requests.clear()
    assert ConservativeAdmission().reserve(scheduler) is None


@pytest.mark.parametrize('candidate,remaining,expected', [(48,32,32),(16,32,16),(32,16,16)])
def test_complete_prefix_budget(candidate, remaining, expected):
    assert ConservativeAdmission().prefix_blocks(candidate, remaining) == expected


@pytest.mark.parametrize('candidate,remaining', [(15,32),(32,0),(31,32)])
def test_partial_chunk_budget_is_rejected(candidate, remaining):
    with pytest.raises(RuntimeError, match='complete chunks'):
        ConservativeAdmission().prefix_blocks(candidate, remaining)


def test_capacity_boundary_preserves_whole_step_reserve():
    policy = ConservativeAdmission()
    assert policy.permits(64,32,32)
    assert not policy.permits(63,32,32)
    assert not policy.permits(909,16,972)


def test_candidate_order_filters_lifecycle_and_uses_front_operation_only():
    pending = {rid: [NS(block_hashes=['later','first']), NS(block_hashes=['ignored'])]
               for rid in ['z','a','active','unfinished','flight','protected','unranked']}
    pending['unranked'][0].block_hashes = ['missing']
    lifecycle = NS(is_finished=lambda rid: rid != 'unfinished',
                   has_in_flight=lambda rid: rid == 'flight')
    before = {rid: tuple(ops) for rid, ops in pending.items()}
    result = ranked_candidates(pending, {'active': object()}, lifecycle, {'protected'},
                               {'first':2,'later':8,'ignored':0})
    assert [(rank,rid) for rank,rid,_ in result] == [(2,'a'),(2,'z')]
    assert result[0][2] is pending['a'][0]
    assert before == {rid: tuple(ops) for rid, ops in pending.items()}
