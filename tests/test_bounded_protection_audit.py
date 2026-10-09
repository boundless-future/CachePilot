from copy import deepcopy
import pytest
from scripts.audit_bounded_protection import audit_rows


def evidence():
    shared = dict(wire_id="w", start=0, end=512, block_ids=[list(range(32))], prefix_sha256="p")
    events = [
        dict(shared, event="protection_prepared", acquired_pins=32, chunk_hashes=["a", "b"], free_after=100, reserve=80),
        dict(event="protection_allocation_boundary", protected_ids=list(range(32)), allocated_ids=[40]),
        dict(shared, event="protection_metadata_committed", owned_pins=32, scheduled_tokens=1),
        dict(shared, event="scheduler_built", source="protection", batch_id="b"),
        dict(shared, event="protection_receipt", released_pins=32, batch_id="b")]
    return [dict(row, monotonic_ns=i) for i, row in enumerate(events)]


def test_complete_conservation_and_audit_is_repeatable():
    rows = evidence()
    before = deepcopy(rows)
    assert audit_rows(rows)["peak_protection_blocks"] == 32
    assert audit_rows(rows)["final_owned_pins"] == 0
    assert rows == before


@pytest.mark.parametrize("index,key,value", [(0,"acquired_pins",33), (0,"free_after",79),
    (1,"allocated_ids",[1]), (2,"scheduled_tokens",0), (2,"prefix_sha256","wrong"),
    (4,"batch_id","stale"), (4,"released_pins",16)])
def test_reject_corrupted_protection_evidence(index, key, value):
    rows = evidence()
    rows[index][key] = value
    with pytest.raises(AssertionError):
        audit_rows(rows)


def test_missing_receipt_and_duplicate_pin_rejected():
    with pytest.raises(AssertionError):
        audit_rows(evidence()[:-1])
    rows = evidence()
    with pytest.raises(AssertionError):
        audit_rows(rows[:1] + rows)
