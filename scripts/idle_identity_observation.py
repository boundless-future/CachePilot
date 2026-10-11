"""Observe an idle-finalization exception without changing its outcome or state."""
import functools

from scripts.owned_service_protocol import wire_id


def snapshot(connector):
    manager = connector._lazy_offload_manager
    clients = connector.scheduler_adapter.req_clients
    rows = []
    for rid, pending in list(manager._policy._pending.items()):
        operations = []
        for entry in pending:
            meta = entry.store_metadata
            operations.append(dict(wire_id=wire_id(meta), start=meta.op.start, end=meta.op.end))
        current = {url: client.identities.get(rid) for url, client in clients.items()}
        identities = {op['wire_id'] for op in operations}
        classification = ('mixed_pending' if len(identities) != 1 else
            'missing_client' if not current or any(value is None for value in current.values()) else
            'different_generation' if any(value not in identities for value in current.values()) else 'matches')
        rows.append(dict(request_id=rid, operations=operations, current=current,
            classification=classification, finished=manager._requests.is_finished(rid),
            inflight=manager._requests.has_in_flight(rid)))
    return rows


def observe(finalize, record):
    @functools.wraps(finalize)
    def wrapped(connector, scheduler):
        try:
            return finalize(connector, scheduler)
        except Exception as original:
            try:
                record(dict(error=repr(original), pending=snapshot(connector)))
            except Exception:
                # Diagnostic I/O must not replace the original failure.
                pass
            raise
    return wrapped
