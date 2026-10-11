"""The budget gate must see a distinct candidate before the real receipt."""
from copy import deepcopy

import pytest

from scripts.audit_protection_inflight_budget import audit_rows


def rows():
    return [
        dict(event="shared_target", monotonic_ns=1, batch_id="batch", request_id="old",
            end=512, block_ids=[list(range(32))]),
        dict(event="shared_gate_installed", monotonic_ns=2),
        dict(event="protection_fallback", monotonic_ns=3, reason="inflight_budget",
            request_id="new", wire_id="new:1", inflight_request_ids=["old"],
            prefix_sha256="hash", start=0, end=256,
            inflight_blocks=32, budget_blocks=32, available_blocks=0,
            required_blocks=16, acquired_pins=0),
        dict(event="shared_gate_opened", monotonic_ns=4),
        dict(event="shared_result_delivered", monotonic_ns=5, actual_result=True),
        dict(event="protection_receipt", monotonic_ns=6, batch_id="batch", released_pins=32),
        dict(event="scheduler_built", monotonic_ns=7, source="default_manager",
            wire_id="new:1", prefix_sha256="hash", start=0, end=256),
    ]


def test_inflight_fallback_precedes_original_receipt():
    assert audit_rows(rows())["distinct_fallbacks"] == 1


@pytest.mark.parametrize("field,value", [
    ("request_id", "old"), ("inflight_blocks", 16), ("available_blocks", 16),
    ("acquired_pins", 16), ("inflight_request_ids", []),
])
def test_rejects_false_budget_exhaustion(field, value):
    events = deepcopy(rows())
    events[2][field] = value
    with pytest.raises(AssertionError):
        audit_rows(events)


def test_rejects_fallback_after_gate_open():
    events = rows()
    events[2]["monotonic_ns"] = 7
    with pytest.raises(AssertionError):
        audit_rows(events)


def test_rejects_missing_default_store():
    with pytest.raises(AssertionError):
        audit_rows(rows()[:-1])
