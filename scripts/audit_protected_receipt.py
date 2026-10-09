"""Require one real protected batch at all delayed/failed receipt edges."""
def audit_rows(rows, mode):
    installed = [r for r in rows if r["event"] == "protected_receipt_probe_installed"]
    assert len(installed) == 1
    injected = installed[0]
    batch_id = injected["batch_id"]
    def edge(event):
        found = [r for r in rows if r["event"] == event and r.get("batch_id") == batch_id]
        assert len(found) == 1, event
        return found[0]
    ready = edge("protected_actual_ready")
    delivered = edge("protected_result_delivered")
    worker = edge("worker_receipt")
    scheduler = edge("scheduler_receipt")
    released = edge("protection_receipt")
    built = edge("scheduler_built")
    assert built["source"] == "protection" and injected["mode"] == mode
    assert built["monotonic_ns"] < injected["monotonic_ns"] <= ready["monotonic_ns"] < delivered["monotonic_ns"] < worker["monotonic_ns"] < released["monotonic_ns"] < scheduler["monotonic_ns"]
    assert delivered["actual_result"] is True
    failed = mode == "failed-after-success"
    assert delivered["delivered_result"] is (not failed)
    assert worker["failed"] is failed and scheduler["failed"] is failed and released["failed"] is failed
    observed = [r for r in rows if r["event"] == "protected_runtime_refs"
                and built["monotonic_ns"] < r["monotonic_ns"] < released["monotonic_ns"]]
    assert any(any(b["request_id"] == built["request_id"] and len(b["refs"]) == 32
                   and all(v >= 1 for v in b["refs"].values()) for b in r["batches"]) for r in observed)
    elapsed = (delivered["monotonic_ns"]-injected["monotonic_ns"]) / 1e9
    if mode == "delayed":
        assert elapsed >= 4.99 and ready["remaining_seconds"] > 0
    return dict(passed=True, mode=mode, batch_id=batch_id, actual_store_succeeded=True,
        receipt_failed=failed, delivery_delay_seconds=elapsed, pin_snapshot_before_receipt=True,
        dma_interruption_test=False)
