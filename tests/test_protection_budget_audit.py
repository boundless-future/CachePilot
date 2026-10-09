import pytest
from scripts.audit_protection_budget import audit_rows


def evidence():
    identity = dict(wire_id="original-generation", prefix_sha256="prefix", start=0, end=512)
    return [dict(identity, event="protection_fallback", reason="candidate_exceeds_budget",
                 required_blocks=32, available_blocks=16, budget_blocks=16,
                 acquired_pins=0, monotonic_ns=10),
            dict(identity, event="scheduler_built", source="default_manager", monotonic_ns=20)]


def test_native_save_after_budget_rejection():
    result = audit_rows(evidence())
    assert result["declined_operations"] == result["default_saved_operations"] == 1


@pytest.mark.parametrize("index,key,value", [
    (0, "acquired_pins", 1), (0, "available_blocks", 32),
    (1, "wire_id", "new-generation"), (1, "prefix_sha256", "different-prefix"),
    (1, "source", "protection"), (1, "monotonic_ns", 5),
])
def test_false_fallback_evidence_rejected(index, key, value):
    rows = evidence()
    rows[index][key] = value
    with pytest.raises(AssertionError):
        audit_rows(rows)


def test_empty_coverage_and_hidden_pin_are_rejected():
    with pytest.raises(AssertionError):
        audit_rows(evidence()[1:])
    with pytest.raises(AssertionError):
        audit_rows(evidence() + [dict(event="protection_prepared")])
