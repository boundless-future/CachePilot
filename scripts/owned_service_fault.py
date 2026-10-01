"""Diagnostic only: real copy enqueue followed by a host exception/stream hold.

This does not simulate a CUDA driver fault or prove an interrupt inside a PCIe
transaction. The unchanged worker receives the actual server false result.
"""
import os
from pathlib import Path


def install_fault(service):
    import torch
    import lmcache.v1.multiprocess.modules.lmcache_driven_transfer as module

    kind = os.environ.get("CACHEPILOT_OWNED_FAULT_KIND")
    if kind not in ("store", "retrieve"):
        raise ValueError("Diagnostic kind must be store or retrieve")
    hold = int(os.environ.get("CACHEPILOT_OWNED_HOLD_CYCLES", "0"))
    if not 0 <= hold <= 40_000_000_000:
        raise ValueError("Diagnostic stream hold outside bounds")
    directory = Path(os.environ["CACHEPILOT_OWNED_SERVICE_DIR"])
    original = module.transfer_kv_per_object_group
    fail = os.environ.get("CACHEPILOT_OWNED_HOST_EXCEPTION", "1") == "1"
    drop = os.environ.get("CACHEPILOT_OWNED_DROP_TERMINAL", "0") == "1"
    limit = int(os.environ.get("CACHEPILOT_OWNED_HOLD_TRANSFERS", "1"))
    if not 1 <= limit <= 3 or (drop and limit != 1):
        raise ValueError("Diagnostic transfer count outside bounds")
    injected = set()
    dropped_payload = None
    if drop:
        terminal = service.terminal

        def observed_terminal(payload):
            if payload == dropped_payload:
                service.record("diagnostic_terminal_dropped", payload.request_id,
                               sequence=payload.sequence, kind=payload.kind)
                try:
                    service.transfer.close()
                except RuntimeError as exc:
                    if "Cannot close allocator/controller with owned consumers" not in str(exc):
                        raise
                    service.record("diagnostic_transfer_close_refused", payload.request_id,
                                   sequence=payload.sequence, error=str(exc))
                else:
                    raise RuntimeError("Transfer dispatcher closed with a live owner")
                return
            return terminal(payload)

        service.terminal = observed_terminal

    def copy(*args, **kwargs):
        nonlocal dropped_payload
        task = getattr(service.local, "task", None)
        selected = (task and task["payload"].kind == kind and len(injected) < limit
                    and task["payload"].sequence not in injected
                    and (directory / "armed-transfer").exists())
        if selected:
            injected.add(task["payload"].sequence)
            if drop:
                dropped_payload = task["payload"]
        result = original(*args, **kwargs)
        if selected:
            # Host registration/staging inside the real copy can synchronize.
            # Hold its terminal afterwards, without claiming DMA interruption.
            if hold:
                torch.cuda._sleep(hold)
            service.record("diagnostic_copy_enqueued", task["owner"].request_id,
                           sequence=task["payload"].sequence, kind=kind,
                           hold_cycles=hold, hold_position="after_real_copy_submit",
                           host_exception=fail, drop_terminal=drop)
            (directory / "copy-enqueued").touch()
            if fail:
                raise RuntimeError("Diagnostic host failure after real CUDA enqueue")
        return result

    module.transfer_kv_per_object_group = copy
