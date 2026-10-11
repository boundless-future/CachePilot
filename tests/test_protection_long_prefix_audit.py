from scripts.audit_protection_long_prefix import audit_rows
import pytest


def chain():
    return [
        dict(monotonic_ns=1, event="scheduler_built", source="protection", wire_id="w",
             batch_id="front", start=0, end=512, block_ids=[list(range(1, 33))]),
        dict(monotonic_ns=2, event="scheduler_receipt", wire_id="w", batch_id="front", failed=False),
        dict(monotonic_ns=3, event="scheduler_built", source="default_manager", wire_id="w",
             batch_id="suffix", start=512, end=1024, block_ids=[list(range(33, 65))]),
        dict(monotonic_ns=4, event="scheduler_receipt", wire_id="w", batch_id="suffix", failed=False),
    ]


def test_accepts_ordered_protected_front_and_native_suffix():
    rows = chain()
    assert audit_rows(rows)["split_chains"] == 1


@pytest.mark.parametrize("mutation", ["early", "duplicate", "missing_receipt", "wrong_generation", "overlap"])
def test_rejects_invalid_split_evidence(mutation):
    rows = chain()
    if mutation == "early":
        rows[2]["monotonic_ns"] = 1.5
    elif mutation == "duplicate":
        rows.append(dict(rows[2], monotonic_ns=5))
    elif mutation == "missing_receipt":
        rows.pop()
    elif mutation == "wrong_generation":
        rows[2]["wire_id"] = "other"
    else:
        rows[2]["block_ids"][0][0] = 1
    with pytest.raises(AssertionError):
        audit_rows(rows)
