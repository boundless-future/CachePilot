from copy import deepcopy

import pytest

from scripts.audit_protection_preemption import audit_rows


def evidence():
    ids = list(range(32))
    held = dict(original_request_id="source", refs={b: 1 for b in ids},
                hashes={b: f"hash-{b}" for b in ids}, manager_inflight=True)
    rows = [dict(event="shared_target", request_id="source", block_ids=[ids], end=512),
        dict(event="shared_gate_installed", request_id="source"),
        dict(event="shared_actual_ready", request_id="source"),
        dict(event="protected_pressure_state", **held),
        dict(event="protected_pressure_allocation", **held, allocated_ids=[100],
            after=dict(batch_id="batch", **held)),
        dict(event="protected_preempt_before", **held, request_id="p0", status="RUNNING",
            num_preemptions=0, computed_tokens=3500, victim_blocks=[100, 101]),
        dict(event="protected_preempt_after", **held, request_id="p0", status="PREEMPTED",
            num_preemptions=1, computed_tokens=0),
        dict(event="protected_pressure_allocation", **held, allocated_ids=[100, 101],
            after=dict(batch_id="batch", **held))]
    rows += [dict(event="protected_pressure_finished", **held, request_id=f"p{i}",
                  status="FINISHED_ABORTED", num_preemptions=int(i == 0)) for i in range(4)]
    rows += [dict(event="shared_gate_opened"), dict(event="shared_result_delivered", actual_result=True),
        dict(event="worker_receipt", failed=False),
        dict(event="shared_receipt_before", refs={b: 1 for b in ids}),
        dict(event="protection_receipt", failed=False, released_pins=32),
        dict(event="scheduler_receipt", failed=False),
        dict(event="shared_receipt_after", refs={b: 0 for b in ids},
            protection_inflight=False, manager_inflight=False)]
    return [dict(dict(request_id="source", batch_id="batch", monotonic_ns=i+1), **r)
            for i, r in enumerate(rows)]


def test_complete_preemption_under_live_protection():
    result = audit_rows(evidence(), {f"p{i}" for i in range(4)})
    assert result["preemptions"] == 1 and not result["shared_prefix_victim"]


@pytest.mark.parametrize("mutation", ["no_preemption", "missing_after", "not_natural",
    "wrong_victim", "shared_victim", "unchanged_count", "not_reset", "early_receipt",
    "overwrite", "changed_hash", "lost_pin", "lost_manager", "double_release",
    "no_original_release", "missing_cancel", "duplicate_receipt", "failed_store"])
def test_reject_false_preemption_evidence(mutation):
    rows = deepcopy(evidence())
    by_event = {r["event"]: r for r in rows}
    if mutation == "no_preemption":
        rows = [r for r in rows if not r["event"].startswith("protected_preempt_")]
    elif mutation == "missing_after":
        rows.remove(by_event["protected_preempt_after"])
    elif mutation == "not_natural":
        by_event["protected_preempt_after"]["status"] = "FINISHED_ABORTED"
    elif mutation == "wrong_victim":
        by_event["protected_preempt_after"]["request_id"] = "p3"
    elif mutation == "shared_victim":
        by_event["protected_preempt_before"]["victim_blocks"].append(0)
    elif mutation == "unchanged_count":
        by_event["protected_preempt_after"]["num_preemptions"] = 0
    elif mutation == "not_reset":
        by_event["protected_preempt_after"]["computed_tokens"] = 1
    elif mutation == "early_receipt":
        by_event["shared_gate_opened"]["monotonic_ns"] = 5
    elif mutation == "overwrite":
        by_event["protected_pressure_allocation"]["allocated_ids"].append(0)
    elif mutation == "changed_hash":
        by_event["protected_preempt_after"]["hashes"] = {**by_event["protected_preempt_after"]["hashes"], 0: "changed"}
    elif mutation == "lost_pin":
        by_event["protected_preempt_after"]["refs"] = {**by_event["protected_preempt_after"]["refs"], 0: 0}
    elif mutation == "lost_manager":
        by_event["protected_preempt_after"]["manager_inflight"] = False
    elif mutation == "double_release":
        by_event["shared_receipt_before"]["refs"][0] = 0
    elif mutation == "no_original_release":
        by_event["shared_receipt_after"]["refs"][0] = 1
    elif mutation == "missing_cancel":
        rows.remove(by_event["protected_pressure_finished"])
    elif mutation == "duplicate_receipt":
        rows.append(dict(by_event["protection_receipt"]))
    else:
        by_event["worker_receipt"]["failed"] = True
    with pytest.raises(AssertionError):
        audit_rows(rows, {f"p{i}" for i in range(4)})
