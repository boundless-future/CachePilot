from copy import deepcopy

import pytest

from scripts.audit_protection_receipt_identity import audit_rows


def evidence():
    ids = list(range(32))
    pinned = dict(refs={b: 1 for b in ids}, hashes={b: f"h{b}" for b in ids},
        protection_inflight=True, manager_inflight=True, ledger_batch_id="batch",
        native_receipt_calls=7, free_blocks=840)
    released = dict(pinned, refs={b: 0 for b in ids}, protection_inflight=False,
        manager_inflight=False, ledger_batch_id=None, native_receipt_calls=8, free_blocks=872)
    rows = [dict(event="shared_target", block_ids=[ids], end=512),
        dict(event="receipt_identity_rejected", case="synthetic_stale_batch",
            injected_batch_id="old", error="Receipt addresses a stale STORE generation",
            before=pinned, after=deepcopy(pinned)),
        dict(event="shared_gate_installed"), dict(event="shared_actual_ready"),
        dict(event="shared_gate_opened"), dict(event="shared_result_delivered", actual_result=True),
        dict(event="worker_receipt", failed=False),
        dict(event="shared_receipt_before", refs=pinned["refs"]),
        dict(event="protection_receipt", released_pins=32, failed=False),
        dict(event="scheduler_receipt", failed=False),
        dict(event="shared_receipt_after", refs=released["refs"]),
        dict(event="receipt_identity_rejected", case="duplicate_after_terminal",
            injected_batch_id="batch", error="Receipt has no original STORE handoff",
            before=released, after=deepcopy(released))]
    return [dict(request_id="source", batch_id="batch", monotonic_ns=i+1, **r)
            for i, r in enumerate(rows)]


def test_rejected_envelopes_preserve_live_state():
    assert audit_rows(evidence())["native_manager_unchanged_on_reject"]


@pytest.mark.parametrize("mutation", ["native_called", "pin_lost", "ledger_retired", "hash_changed",
    "valid_identity", "no_duplicate", "early_duplicate", "wrong_batch", "twice", "failed_original"])
def test_invalid_identity_evidence_fails(mutation):
    rows = deepcopy(evidence())
    stale, duplicate = rows[1], rows[-1]
    if mutation == "native_called":
        stale["after"]["native_receipt_calls"] += 1
    elif mutation == "pin_lost":
        stale["after"]["refs"][0] = 0
    elif mutation == "ledger_retired":
        stale["after"]["ledger_batch_id"] = None
    elif mutation == "hash_changed":
        stale["after"]["hashes"][0] = "bad"
    elif mutation == "valid_identity":
        stale["injected_batch_id"] = "batch"
    elif mutation == "no_duplicate":
        rows.pop()
    elif mutation == "early_duplicate":
        duplicate["monotonic_ns"] = 4
    elif mutation == "wrong_batch":
        duplicate["batch_id"] = "other"
    elif mutation == "twice":
        rows.append(dict(rows[8]))
    else:
        rows[8]["failed"] = True
    with pytest.raises(AssertionError):
        audit_rows(rows)
