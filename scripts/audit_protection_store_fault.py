"""Cross-process proof of a protected STORE's original server false result.

The induced host exception occurs after the CUDA copy submission. LMCache
contains it within the transfer implementation, so ``transfer_retired`` is the
server-to-worker failure boundary rather than OwnedService's outer exception
callback.
"""
import json


def audit_rows(handoffs, service):
    armed = [r for r in handoffs if r["event"] == "protected_server_fault_armed"]
    assert len(armed) == 1, "Expected one targeted protected batch"
    target = armed[0]
    assert target["source"] == "protection"
    def edge(rows, event, key, identity):
        found = [r for r in rows if r["event"] == event and r.get(key) == identity]
        assert len(found) == 1, event
        return found[0]
    receipt = {name: edge(handoffs, name, "batch_id", target["batch_id"]) for name in (
        "protected_server_fault_result", "worker_receipt", "protection_receipt", "scheduler_receipt")}
    copy = edge(service, "diagnostic_copy_enqueued", "request_id", target["wire_id"])
    assert copy["kind"] == "store" and copy["host_exception"] is True
    assert copy["hold_position"] == "after_real_copy_submit" and not copy["drop_terminal"]
    transfer = [r for r in service if r.get("sequence") == copy["sequence"]]
    submitted = edge(transfer, "transfer_submitted", "request_id", target["wire_id"])
    terminal = edge(transfer, "terminal_seen", "request_id", target["wire_id"])
    retired = edge(transfer, "transfer_retired", "request_id", target["wire_id"])
    assert submitted["blocks"] == target["block_ids"]
    assert retired["succeeded"] is False
    result = receipt["protected_server_fault_result"]
    assert result["actual_result"] is False and result["delivered_result"] is False
    for name in ("worker_receipt", "protection_receipt", "scheduler_receipt"):
        assert receipt[name]["failed"] is True
    assert receipt["protection_receipt"]["released_pins"] == sum(map(len, target["block_ids"]))
    # LMCache catches the injected host exception inside its transfer path and
    # reports it by retiring the native request with ``succeeded=False``.  It
    # therefore need not escape to OwnedService's outer diagnostic callback.
    ordered = [target, submitted, copy, terminal, retired]
    assert all(a["monotonic_ns"] < b["monotonic_ns"] for a, b in zip(ordered, ordered[1:]))
    ordered = [target, result, receipt["worker_receipt"], receipt["protection_receipt"], receipt["scheduler_receipt"]]
    assert all(a["monotonic_ns"] < b["monotonic_ns"] for a, b in zip(ordered, ordered[1:]))
    # Host callback retirement and client event/RPC consumption are independent
    # completion channels; neither is assumed to precede the other here.
    return dict(passed=True, batch_id=target["batch_id"], wire_id=target["wire_id"],
        server_failure_after_real_enqueue=True, future_value_overridden=False,
        original_future_result=False, native_terminal_seen=True,
        released_protection_pins=receipt["protection_receipt"]["released_pins"],
        driver_fault_test=False, dma_interruption_test=False)


def audit(directory):
    def read(pattern):
        return [json.loads(line) for p in (directory / "events").glob(pattern)
                for line in p.read_text().splitlines()]
    return audit_rows(read("handoff-*.jsonl"), read("owned-service-*.jsonl"))
