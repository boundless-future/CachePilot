from scripts.audit_protection_store_fault import audit_rows


def handoff(event, tick, **fields):
    return dict(event=event, monotonic_ns=tick, **fields)


def service(event, tick, **fields):
    return dict(event=event, monotonic_ns=tick, **fields)


def valid_rows():
    batch = dict(batch_id="b", wire_id="cp1:1:1:" + "a" * 32 + ":raw",
                 request_id="raw", source="protection", block_ids=[[4] * 32])
    handoffs = [
        handoff("protected_server_fault_armed", 1, **batch),
        handoff("protected_server_fault_result", 8, **batch, actual_result=False,
                delivered_result=False),
        handoff("worker_receipt", 9, **batch, failed=True),
        handoff("protection_receipt", 10, **batch, failed=True, released_pins=32),
        handoff("scheduler_receipt", 11, **batch, failed=True),
    ]
    wire = batch["wire_id"]
    rows = [
        service("transfer_submitted", 2, request_id=wire, sequence=7, kind="store",
                blocks=batch["block_ids"]),
        service("diagnostic_copy_enqueued", 3, request_id=wire, sequence=7, kind="store",
                host_exception=True, hold_position="after_real_copy_submit", drop_terminal=False),
        service("terminal_seen", 4, request_id=wire, sequence=7, kind="store"),
        service("transfer_retired", 5, request_id=wire, sequence=7, kind="store", succeeded=False),
    ]
    return handoffs, rows


def test_real_server_false_result_requires_copy_and_terminal():
    result = audit_rows(*valid_rows())
    assert result["passed"] and result["original_future_result"] is False
    assert result["released_protection_pins"] == 32


def test_rejects_receipt_rewrite_and_missing_terminal():
    handoffs, rows = valid_rows()
    handoffs[1]["actual_result"] = True
    try:
        audit_rows(handoffs, rows)
    except AssertionError:
        pass
    else:
        raise AssertionError("accepted rewritten receipt")
    handoffs, rows = valid_rows()
    rows = [r for r in rows if r["event"] != "terminal_seen"]
    try:
        audit_rows(handoffs, rows)
    except AssertionError:
        pass
    else:
        raise AssertionError("accepted missing native terminal")
