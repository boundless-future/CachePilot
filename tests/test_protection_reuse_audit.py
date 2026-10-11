from copy import deepcopy

import pytest

from scripts.audit_protection_reuse import audit_rows


def evidence(old_failed=True):
    rid = "cmpl-repeat-0"
    oldwire, newwire = f"cp1:7:1:{'a'*32}:{rid}", f"cp1:7:2:{'b'*32}:{rid}"
    old = dict(request_id=rid, batch_id="old", wire_id=oldwire, source="protection",
        end=512, block_ids=[list(range(32))], prefix_sha256="old-prefix")
    new = dict(old, batch_id="new", wire_id=newwire, block_ids=[list(range(32, 64))],
        prefix_sha256="new-prefix")
    refs = {str(i): 1 for i in range(32)}
    state = dict(orphaned=True, manager_inflight=True, protection_inflight=True,
        ledger=old, refs=refs, pending=[], broken=False, native_receipt_calls=2)
    pending = [dict(wire_id=newwire)]
    before = dict(state, pending=pending)
    after = dict(before, orphaned=False, manager_inflight=False, protection_inflight=False,
        ledger=None, refs={str(i): 0 for i in range(32)}, native_receipt_calls=3)
    active_new = dict(after, manager_inflight=True, protection_inflight=True, pending=[],
        ledger=new, refs={str(i): int(i >= 32) for i in range(64)})
    rows = [dict(event="reuse_target", **old),
        dict(event="reuse_new_generation", request_id=rid, old_wire_id=oldwire,
            new_wire_id=newwire, state=state),
        dict(event="reuse_new_finished", request_id=rid, status="FINISHED_LENGTH_CAPPED", state=before),
        dict(event="reuse_result", batch_id="old", actual_result=True, delivered_result=not old_failed),
        dict(event="reuse_receipt_before", batch_id="old", state=before),
        dict(event="scheduler_receipt", batch_id="old", failed=old_failed),
        dict(event="reuse_receipt_after", batch_id="old", state=after),
        dict(event="reuse_old_rejected", request_id=rid, old_batch_id="old", new_batch_id=None,
            case="duplicate_after_old_terminal", before=after, after=deepcopy(after), error="no original"),
        dict(event="reuse_new_batch", **new)]
    for case in ("old_success", "old_failure", "duplicate_old_success"):
        rows.append(dict(event="reuse_old_rejected", request_id=rid, old_batch_id="old", new_batch_id="new",
            case=case, before=deepcopy(active_new), after=deepcopy(active_new), error="stale"))
    rows.append(dict(event="scheduler_receipt", batch_id="new", failed=False))
    driver = dict(actual_request_id_reuse=True, vllm_request_id_randomization_disabled=True,
        payload=dict(request_id="repeat"), response=dict(id="cmpl-repeat"))
    return [dict(monotonic_ns=i+1, **r) for i, r in enumerate(rows)], driver


@pytest.mark.parametrize("old_failed", [False, True])
def test_real_reuse_keeps_successor_pending(old_failed):
    rows, driver = evidence(old_failed)
    assert audit_rows(rows, driver, old_failed)["new_pending_preserved"]


@pytest.mark.parametrize("bad", ["same_wire", "same_nonce", "api_id_only", "not_orphaned",
    "lost_pending", "broken_prefix", "wrong_native_count", "lost_new_pin", "mutated_on_reject",
    "missing_reject", "duplicate_receipt", "failed_successor", "early_new_batch", "old_not_released"])
def test_incomplete_or_corrupted_cross_generation_evidence_fails(bad):
    rows, driver = deepcopy(evidence())
    def first(event):
        return next(r for r in rows if r["event"] == event)
    if bad == "same_wire":
        first("reuse_new_generation")["new_wire_id"] = rows[0]["wire_id"]
    elif bad == "same_nonce":
        first("reuse_new_generation")["new_wire_id"] = first("reuse_new_generation")["new_wire_id"].replace("b"*32, "a"*32)
    elif bad == "api_id_only":
        driver["payload"]["request_id"] = "different"
    elif bad == "not_orphaned":
        first("reuse_new_generation")["state"]["orphaned"] = False
    elif bad == "lost_pending":
        first("reuse_receipt_after")["state"]["pending"] = []
    elif bad == "broken_prefix":
        first("reuse_receipt_after")["state"]["broken"] = True
    elif bad == "wrong_native_count":
        first("reuse_receipt_after")["state"]["native_receipt_calls"] = 5
    elif bad == "lost_new_pin":
        rows[-2]["before"]["refs"]["32"] = 0
        rows[-2]["after"]["refs"]["32"] = 0
    elif bad == "mutated_on_reject":
        rows[-2]["after"]["native_receipt_calls"] += 1
    elif bad == "missing_reject":
        rows.pop(-2)
    elif bad == "duplicate_receipt":
        rows.append(dict(rows[-1]))
    elif bad == "failed_successor":
        rows[-1]["failed"] = True
    elif bad == "early_new_batch":
        first("reuse_new_batch")["monotonic_ns"] = 2
    else:
        first("reuse_receipt_after")["state"]["refs"]["0"] = 1
    with pytest.raises(AssertionError):
        audit_rows(rows, driver, True)
