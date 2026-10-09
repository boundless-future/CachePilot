from copy import deepcopy
import pytest
from scripts.audit_protection_handoff import STAGES, audit_rows


def evidence():
    common = dict(request_id="r", batch_id="b", wire_id="original", direction="STORE",
        start=0, end=256, skip=0, block_ids=[list(range(16))], prefix_sha256="prefix",
        source="default_manager", failed=False)
    return [dict(common, event=stage, monotonic_ns=i+1) for i, stage in enumerate(STAGES)]


def test_all_real_edges_required():
    rows = evidence()
    assert audit_rows(rows)["batches"] == 1
    for i in range(len(rows)):
        with pytest.raises(AssertionError):
            audit_rows(rows[:i] + rows[i+1:])
    with pytest.raises(AssertionError):
        audit_rows(rows + [rows[-1]])


@pytest.mark.parametrize("field,value", [("wire_id", "new-generation"), ("start", 256),
    ("block_ids", [[99]]), ("prefix_sha256", "changed"), ("monotonic_ns", 0)])
def test_corrupt_worker_handoff_rejected(field, value):
    rows = deepcopy(evidence())
    rows[1][field] = value
    with pytest.raises(AssertionError):
        audit_rows(rows)


def test_failure_and_added_protection_not_silently_accepted():
    rows = evidence()
    rows[-1]["failed"] = True
    with pytest.raises(AssertionError):
        audit_rows(rows)
    rows[-2]["failed"] = True
    assert audit_rows(rows)["failed_batches"] == 1
    with pytest.raises(AssertionError):
        audit_rows(evidence() + [dict(event="protection_bypass", added_stores=1, acquired_protection_pins=0)])
