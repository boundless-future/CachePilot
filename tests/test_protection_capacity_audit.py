"""Reject capacity claims caused by budget exhaustion or missing default handoff."""
from copy import deepcopy

import pytest

from scripts.audit_protection_capacity import audit_rows


def rows():
    return [
        dict(event="protection_fallback", monotonic_ns=1, reason="capacity",
             request_id="old", wire_id="old:1", prefix_sha256="hash",
             start=0, end=512, required_blocks=32, available_blocks=32,
             inflight_blocks=0, budget_blocks=32, free=40, reserve=60,
             acquired_pins=0),
        dict(event="scheduler_built", monotonic_ns=2, source="default_manager",
             wire_id="old:1", prefix_sha256="hash", start=0, end=512),
    ]


def test_capacity_decline_then_native_save():
    assert audit_rows(rows())["default_saved_candidates"] == 1


@pytest.mark.parametrize("field,value", [
    ("available_blocks", 0), ("inflight_blocks", 32), ("free", 100),
    ("acquired_pins", 16), ("required_blocks", 48), ("end", 256),
])
def test_rejects_false_capacity_claim(field, value):
    events = deepcopy(rows())
    events[0][field] = value
    with pytest.raises(AssertionError):
        audit_rows(events)


def test_rejects_missing_native_save():
    with pytest.raises(AssertionError):
        audit_rows(rows()[:-1])


def test_rejects_earlier_or_different_native_save():
    events = rows()
    events[1]["monotonic_ns"] = 0
    with pytest.raises(AssertionError):
        audit_rows(events)
    events[1]["monotonic_ns"] = 2
    events[1]["wire_id"] = "another:1"
    with pytest.raises(AssertionError):
        audit_rows(events)
