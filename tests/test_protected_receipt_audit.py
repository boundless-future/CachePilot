from copy import deepcopy
import pytest
from scripts.audit_protected_receipt import audit_rows


def evidence(mode):
    failed = mode == "failed-after-success"
    rows = [dict(event="scheduler_built", source="protection", request_id="r", monotonic_ns=1),
        dict(event="protected_receipt_probe_installed", mode=mode, monotonic_ns=2),
        dict(event="protected_actual_ready", remaining_seconds=4, monotonic_ns=3),
        dict(event="protected_result_delivered", actual_result=True, delivered_result=not failed, monotonic_ns=5_000_000_003),
        dict(event="worker_receipt", failed=failed, monotonic_ns=5_000_000_004),
        dict(event="protection_receipt", failed=failed, monotonic_ns=5_000_000_005),
        dict(event="scheduler_receipt", failed=failed, monotonic_ns=5_000_000_006)]
    rows = [dict(r, batch_id="b") for r in rows]
    rows.append(dict(event="protected_runtime_refs", monotonic_ns=4,
        batches=[dict(request_id="r", refs={i: 1 for i in range(32)})]))
    return rows


@pytest.mark.parametrize("mode", ["delayed", "failed-after-success"])
def test_all_receipt_edges(mode):
    rows = evidence(mode)
    assert audit_rows(rows, mode)["passed"]
    for index in range(len(rows)):
        with pytest.raises(AssertionError):
            audit_rows(rows[:index]+rows[index+1:], mode)


def test_early_delivery_and_unpinned_snapshot_rejected():
    for mutation in ("early", "unpin", "identity"):
        rows = deepcopy(evidence("delayed"))
        if mutation == "early":
            rows[3]["monotonic_ns"] = 4
        elif mutation == "unpin":
            rows[-1]["batches"][0]["refs"][0] = 0
        else:
            rows[4]["batch_id"] = "stale"
        with pytest.raises(AssertionError):
            audit_rows(rows, "delayed")
