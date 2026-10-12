"""Opt-in scheduler owner bridge for pinned LazyOffloadManager.

No global monkeypatches. This facade owns no GPU pins: native manager does.
Only zero-protection default-manager drains are supported. Caller must provide
validated original handoff IDs before applying worker receipts and must seal
producer generations separately after lookup cleanup and request teardown.
"""
from contextlib import contextmanager
from scripts.owned_service_protocol import wire_id


def operation_key(metadata):
    return (wire_id(metadata), metadata.op.start, metadata.op.end)


class GenerationManagerBridge:
    def __init__(self, manager, ledger):
        if ((manager._policy is not None and manager._policy._pending)
                or manager._requests.in_flight_request_ids()):
            raise RuntimeError('Install ledger before manager admission')
        self.manager, self.ledger = manager, ledger
        self.pending = {}
        self.inflight = {}
        self.batch_serial = 0
        self.receipt_ids = None

    def __getattr__(self, name):
        return getattr(self.manager, name)

    def _pending_keys(self):
        result = set()
        for ops in self.manager._policy._pending.values():
            for pending in ops:
                key = operation_key(pending.store_metadata)
                if key in result:
                    raise RuntimeError('Duplicate native pending range')
                result.add(key)
        return result

    def _drop_removed(self):
        native = self._pending_keys()
        if not native <= self.pending.keys():
            raise RuntimeError('Native pending operation bypassed ledger')
        for key in set(self.pending) - native:
            self.ledger.drop_pending(self.pending.pop(key))

    def add_store_candidate(self, metadata):
        wire = wire_id(metadata)
        if self.ledger.current.get(metadata.request_id) != wire:
            raise RuntimeError('STORE admission requires registered tracker generation')
        key = operation_key(metadata)
        if key in self.pending:
            raise RuntimeError('Duplicate STORE admission')
        ticket = self.ledger.begin(wire, 'pending_store')
        # If native admission raises after mutation, keep unresolved ticket.
        self.pending[key] = ticket
        self.manager.add_store_candidate(metadata)
        self._drop_removed()

    def on_request_reset(self, request_id):
        result = self.manager.on_request_reset(request_id)
        self._drop_removed()
        return result

    def on_request_arrived(self, request_id):
        result = self.manager.on_request_arrived(request_id)
        self._drop_removed()
        return result

    def on_request_finished(self, request_id):
        return self.manager.on_request_finished(request_id)

    def on_scheduler_step(self, output):
        actions = self.manager.on_scheduler_step(output)
        for metadata in actions.stores_to_submit:
            rid, wire = metadata.request_id, wire_id(metadata)
            if rid in self.inflight:
                raise RuntimeError('Overlapping native batch')
            keys = sorted(k for k in self.pending if k[0] == wire
                          and metadata.op.start <= k[1] and k[2] <= metadata.op.end)
            cursor = metadata.op.start
            for key in keys:
                if key[1] != cursor:
                    raise RuntimeError('STORE output has unregistered range')
                cursor = key[2]
            if not keys or cursor != metadata.op.end:
                raise RuntimeError('STORE output does not cover registered pending')
            tickets = tuple(self.pending.pop(k) for k in keys)
            for ticket in tickets:
                self.ledger.submit_store(ticket)
            self.batch_serial += 1
            batch_id = f'{wire}:ledger:{self.batch_serial}'
            self.inflight[rid] = (batch_id, tickets)
        self._drop_removed()
        return actions

    def on_store_results(self, *args, **kwargs):
        if self.receipt_ids is None:
            raise RuntimeError('Unversioned STORE receipt is not allowed')
        return self.apply_receipts(*args, **kwargs, batch_ids=self.receipt_ids)

    @contextmanager
    def receipt_scope(self, batch_ids):
        if self.receipt_ids is not None:
            raise RuntimeError('Nested receipt processing')
        self.receipt_ids = dict(batch_ids)
        try:
            yield
        finally:
            self.receipt_ids = None

    def _release_session(self, request_id):
        # The separate idle-finalize candidate drops policy entries before
        # invoking this method. Preserve its identity preflight and account
        # only operations actually removed from the native policy.
        self._drop_removed()
        return self.manager._release_session(request_id)

    def apply_receipts(self, failed, completed, batch_ids):
        # Validate the complete envelope before the native manager can free
        # any pin. raw ID is only a lookup index, never completion authority.
        if set(batch_ids) != set(completed) or not set(failed) <= set(completed):
            raise ValueError('Invalid receipt identity sets')
        captured = {}
        for rid, count in completed.items():
            if type(count) is not int or count != 1:
                raise ValueError('Single worker completion required')
            batch = self.inflight.get(rid)
            if batch is None or batch[0] != batch_ids[rid]:
                raise ValueError('Stale or missing original batch identity')
            if not self.manager._requests.has_in_flight(rid):
                raise RuntimeError('Native batch missing before receipt')
            captured[rid] = batch
        actions = self.manager.on_store_results(failed, completed)
        for rid, batch in captured.items():
            if self.manager._requests.has_in_flight(rid):
                raise RuntimeError('Single-worker native batch did not settle')
            for ticket in batch[1]:
                self.ledger.terminal(ticket)
            del self.inflight[rid]
        self._drop_removed()
        return actions
