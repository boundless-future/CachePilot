from copy import deepcopy

import pytest

from scripts.audit_protection_shared_preemption import audit_rows


def evidence():
    ids = list(range(32))
    held = dict(original_request_id="source", hashes={b: f"hash-{b}" for b in ids},
        manager_inflight=True)
    one = dict(held, refs={b: 1 for b in ids})
    two = dict(held, refs={b: 2 for b in ids})
    rows = [dict(event="shared_target", request_id="source", block_ids=[ids],
        token_ids=[42] * 512, end=512),
        dict(event="shared_gate_installed", request_id="source"),
        dict(event="shared_actual_ready", request_id="source")]
    rows += [dict(event="protected_pressure_arrived", request_id=f"p{i}",
        first_token=42 if i == 3 else 1000+i, prompt_tokens=3500, **one)
        for i in range(4)]
    rows += [dict(event="protected_pressure_allocation", allocated_ids=[99],
        after=dict(batch_id="batch", **two), **two),
        dict(event="protected_preempt_before", request_id="p3", status="RUNNING",
        num_preemptions=0, computed_tokens=3500, victim_blocks=ids+[99], **two),
        dict(event="protected_preempt_after", request_id="p3", status="PREEMPTED",
        num_preemptions=1, computed_tokens=0, **one),
        dict(event="protected_pressure_allocation", allocated_ids=[99],
        after=dict(batch_id="batch", **one), **one)]
    rows += [dict(event="protected_pressure_finished", request_id=f"p{i}",
        status="FINISHED_ABORTED", **one) for i in range(4)]
    rows += [dict(event="shared_gate_opened"), dict(event="shared_result_delivered", actual_result=True),
        dict(event="worker_receipt", failed=False),
        dict(event="shared_receipt_before", refs={b: 1 for b in ids}),
        dict(event="protection_receipt", failed=False, released_pins=32),
        dict(event="scheduler_receipt", failed=False),
        dict(event="shared_receipt_after", refs={b: 0 for b in ids},
            protection_inflight=False, manager_inflight=False)]
    return [dict(dict(batch_id="batch", monotonic_ns=i+1), **r)
            for i, r in enumerate(rows)]


def test_shared_victim_releases_only_its_references():
    assert audit_rows(evidence(), {f"p{i}" for i in range(4)})["shared_refs_sequence"] == [2, 1, 0]


@pytest.mark.parametrize("mutation", ["no_preemption", "wrong_victim", "partial_sharing",
    "no_shared_ref", "original_lost", "alloc_overwrite", "hash_changed", "manager_lost",
    "early_receipt", "no_original_release", "wrong_finished", "duplicate_receipt"])
def test_invalid_shared_preemption_fails(mutation):
    rows = deepcopy(evidence())
    by_event = {r["event"]: r for r in rows}
    if mutation == "no_preemption":
        rows.remove(by_event["protected_preempt_before"])
    elif mutation == "wrong_victim":
        by_event["protected_preempt_after"]["request_id"] = "p2"
    elif mutation == "partial_sharing":
        by_event["protected_preempt_before"]["victim_blocks"].remove(0)
    elif mutation == "no_shared_ref":
        by_event["protected_preempt_before"]["refs"][0] = 1
    elif mutation == "original_lost":
        by_event["protected_preempt_after"]["refs"][0] = 0
    elif mutation == "alloc_overwrite":
        by_event["protected_pressure_allocation"]["allocated_ids"].append(0)
    elif mutation == "hash_changed":
        by_event["protected_preempt_after"]["hashes"] = {
            **by_event["protected_preempt_after"]["hashes"], 0: "changed"}
    elif mutation == "manager_lost":
        by_event["protected_preempt_after"]["manager_inflight"] = False
    elif mutation == "early_receipt":
        by_event["shared_gate_opened"]["monotonic_ns"] = 8
    elif mutation == "no_original_release":
        by_event["shared_receipt_after"]["refs"][0] = 1
    elif mutation == "wrong_finished":
        by_event["protected_pressure_finished"]["status"] = "FINISHED_LENGTH_CAPPED"
    else:
        rows.append(dict(by_event["protection_receipt"]))
    with pytest.raises(AssertionError):
        audit_rows(rows, {f"p{i}" for i in range(4)})
