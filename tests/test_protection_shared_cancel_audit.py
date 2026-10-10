from copy import deepcopy
import json

import pytest

from scripts.audit_protection_shared_cancel import audit, audit_rows


def evidence():
    ids = list(range(32))
    rows = [dict(event="shared_target", block_ids=[ids], end=512),
        dict(event="shared_gate_installed"), dict(event="shared_actual_ready"),
        dict(event="shared_live_refs", request_id="new", shared_ids=ids, refs={b: 2 for b in ids}),
        dict(event="shared_free_before", request_id="new", original_request_id="old",
             status="FINISHED_ABORTED", shared_ids=ids, protected_ids=ids, refs={b: 2 for b in ids}),
        dict(event="shared_free_after", request_id="new", original_request_id="old",
             status="FINISHED_ABORTED", shared_ids=ids, protected_ids=ids, refs={b: 1 for b in ids},
             allocation_present=False, protection_inflight=True, manager_inflight=True),
        dict(event="shared_gate_opened"), dict(event="shared_result_delivered", actual_result=True),
        dict(event="worker_receipt", failed=False),
        dict(event="shared_receipt_before", refs={b: 1 for b in ids}),
        dict(event="protection_receipt", failed=False, released_pins=32),
        dict(event="scheduler_receipt", failed=False),
        dict(event="shared_receipt_after", refs={b: 0 for b in ids},
             protection_inflight=False, manager_inflight=False)]
    return [dict(request_id="old", batch_id="batch", monotonic_ns=i + 1, **r)
            if "request_id" not in r else dict(batch_id="batch", monotonic_ns=i + 1, **r)
            for i, r in enumerate(rows)]


def test_complete_original_receipt_and_shared_cancel():
    rows = evidence()
    assert audit_rows(rows)["refs_sequence"] == [2, 1, 0]
    for i in range(len(rows)):
        with pytest.raises(AssertionError):
            audit_rows(rows[:i] + rows[i+1:])


@pytest.mark.parametrize("mutation", ["early_gate", "wrong_batch", "wrong_request", "natural_finish",
    "partial_prefix", "cancel_unpins_original", "receipt_does_not_unpin", "duplicate_receipt",
    "missing_manager_owner", "failed_store"])
def test_false_coverage_is_rejected(mutation):
    rows = deepcopy(evidence())
    by_event = {r["event"]: r for r in rows}
    if mutation == "early_gate":
        by_event["shared_gate_opened"]["monotonic_ns"] = 5
    elif mutation == "wrong_batch":
        by_event["worker_receipt"]["batch_id"] = "other"
    elif mutation == "wrong_request":
        by_event["shared_free_after"]["request_id"] = "other"
    elif mutation == "natural_finish":
        by_event["shared_free_before"]["status"] = "FINISHED_LENGTH_CAPPED"
    elif mutation == "partial_prefix":
        by_event["shared_free_before"]["shared_ids"] = [0]
    elif mutation == "cancel_unpins_original":
        by_event["shared_free_after"]["refs"][0] = 0
    elif mutation == "receipt_does_not_unpin":
        by_event["shared_receipt_after"]["refs"][0] = 1
    elif mutation == "duplicate_receipt":
        rows.append(dict(by_event["protection_receipt"]))
    elif mutation == "missing_manager_owner":
        by_event["shared_free_after"]["manager_inflight"] = False
    else:
        by_event["shared_result_delivered"]["actual_result"] = False
    with pytest.raises(AssertionError):
        audit_rows(rows)


@pytest.mark.parametrize("coverage", ["complete", "partial", "wrong_batch", "wrong_range"])
def test_original_batch_requires_every_complete_chunk(tmp_path, monkeypatch, coverage):
    from scripts import audit_bounded_kv
    (tmp_path / "events").mkdir()
    (tmp_path / "events/handoff-test.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in evidence()))
    sources = [dict(batch_id="batch", start=0, end=256), dict(batch_id="batch", start=256, end=512)]
    if coverage == "partial":
        sources.pop()
    elif coverage == "wrong_batch":
        sources[1]["batch_id"] = "other"
    elif coverage == "wrong_range":
        sources[1]["end"] = 496
    monkeypatch.setattr(audit_bounded_kv, "audit", lambda directory: dict(strict_sources=sources))
    if coverage == "complete":
        assert len(audit(tmp_path)["strict_sources"]) == 2
    else:
        with pytest.raises(AssertionError, match="complete strict"):
            audit(tmp_path)
