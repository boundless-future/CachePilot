import pytest

from scripts.diagnose_protection_coverage import inspect_events


def fallback():
    return dict(event="protection_fallback", reason="capacity", wire_id="generation-1", monotonic_ns=10,
        required_blocks=16, available_blocks=16, inflight_blocks=0, budget_blocks=16,
        free=100, reserve=110, acquired_pins=0)


def test_fallback_pairs_only_later_default_store_of_same_generation():
    report = inspect_events([fallback(), dict(event="scheduler_built", source="default_manager",
        wire_id="generation-1", monotonic_ns=11), dict(event="scheduler_built", source="default_manager",
        wire_id="generation-2", monotonic_ns=11)])
    assert report["passed"]
    assert report["observations"][0]["later_default_batches"] == 1
    assert report["observations"][0]["reserve_shortfall"] == 26


@pytest.mark.parametrize("field,value", [("free", 126), ("acquired_pins", 1),
    ("available_blocks", 0), ("budget_blocks", 32), ("required_blocks", 1), ("reserve", -1)])
def test_false_capacity_explanations_fail(field, value):
    event = fallback()
    event[field] = value
    assert not inspect_events([event])["passed"]


def test_no_observations_or_duplicate_identity_cannot_pass():
    assert not inspect_events([])["passed"]
    assert not inspect_events([fallback(), fallback()])["passed"]
