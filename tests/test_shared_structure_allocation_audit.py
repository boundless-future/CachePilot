"""Reject mispaired scheduler steps and fabricated physical allocation totals."""
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_shared_structure_allocation import audit_allocations


def fixture():
    windows = [dict(free=20, reserve=40, monotonic_ns=10),
               dict(free=18, reserve=None, monotonic_ns=30)]
    allocations = [dict(step=1, free=20, reserve=40, monotonic_ns=20,
        calls=[dict(count=2, block_ids=[1, 2], free_before=20, free_after=18)],
        allocated=2, scheduled_tokens=0, free_after=18, usable_blocks=32),
        dict(step=2, free=18, reserve=None, monotonic_ns=40, calls=[],
        allocated=0, scheduled_tokens=1, free_after=18, usable_blocks=32)]
    return windows, allocations


def test_zero_compute_token_allocations_remain_visible():
    checks, summary = audit_allocations(*fixture())
    assert all(checks.values())
    assert summary['zero_token_allocation_steps'] == 1
    assert summary['gross_allocated'] == 2
    assert summary['known_reserve_steps'] == 1
    assert summary['reserve_above_total_pool_steps'] == 1


def test_missing_or_repeated_step_is_not_closed():
    w, a = fixture()
    assert not audit_allocations(w, a[:1])[0]['allocations_observed']
    a[1]['step'] = 1
    assert not audit_allocations(w, a)[0]['allocation_step_sequence']


def test_timestamp_and_window_context_must_match():
    w, a = fixture()
    for key, value in [('monotonic_ns', 31), ('free', 19), ('reserve', 41)]:
        corrupt = deepcopy(a)
        corrupt[0][key] = value
        assert not audit_allocations(w, corrupt)[0]['allocation_window_boundaries']


def test_count_requires_distinct_ids_and_real_free_queue_consumption():
    w, a = fixture()
    for key, value in [('count', 3), ('block_ids', [1, 1]), ('free_after', 19)]:
        corrupt = deepcopy(a)
        corrupt[0]['calls'][0][key] = value
        assert not audit_allocations(w, corrupt)[0]['allocation_counts']


def test_no_observations_never_pass_and_capacity_not_hardcoded():
    assert not all(audit_allocations([], [])[0].values())
    w, a = fixture()
    a[1]['usable_blocks'] = 64
    assert not audit_allocations(w, a)[0]['physical_pool_bounds']


def test_reserve_exceeded_is_reported_not_hidden_or_called_safety_failure():
    w, a = fixture()
    w[0]['reserve'] = a[0]['reserve'] = 1
    checks, summary = audit_allocations(w, a)
    assert all(checks.values())
    assert summary['reserve_exceeded_steps'] == 1
