"""CPU-contract candidate only; not selected by a service connector.

Native protection still registers/frees pins. A failed commit poisons the
generation rather than guessing how much native mutation occurred.
"""
from scripts.generation_manager_bridge import GenerationManagerBridge, operation_key
from scripts.generation_session_ledger import GenerationSessionLedger, Ticket
from dataclasses import replace
from scripts.bounded_protection import BoundedProtection


class GenerationBoundedProtection(BoundedProtection):
    """CPU candidate: retain pins after an ambiguous native commit."""

    def bind(self, kv_manager):
        raise RuntimeError('Generation protection serving integration is not enabled')

    def prepare(self, scheduler):
        self.manager._healthy()
        return super().prepare(scheduler)

    def rollback(self, reason):
        # register_batch may have acquired ownership before raising, while
        # the helper still labels the pins PREPARED. Never free those pins.
        self.manager._healthy()
        return super().rollback(reason)


class ProtectionGenerationLedger(GenerationSessionLedger):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.derived_from = {}

    def derive_suffix(self, parent):
        generation = self.generations[parent.wire]
        if (parent.kind != 'pending_store' or generation.operations.get(parent) != 'pending'
                or generation.end_state != 'open' or generation.error is not None):
            raise RuntimeError('Only an original pending reservation can split')
        if self.serial >= self.operation_capacity:
            raise RuntimeError('Ledger operation lifetime capacity exhausted')
        self.serial += 1
        child = Ticket(parent.wire, self.serial, 'pending_store')
        generation.operations[child] = 'pending'
        self.derived_from[child] = parent
        return child

    def ready(self, wire):
        return self.generations[wire].error is None and super().ready(wire)


class GenerationProtectionBridge(GenerationManagerBridge):
    def __init__(self, manager, ledger):
        if not isinstance(ledger, ProtectionGenerationLedger):
            raise TypeError('Protection-aware ledger required')
        super().__init__(manager, ledger)
        self.splits = []
        self.poisoned = None

    def _healthy(self):
        if self.poisoned is not None:
            raise RuntimeError('Unresolved protection commit requires instance reconstruction')

    def on_scheduler_step(self, output):
        self._healthy()
        return super().on_scheduler_step(output)

    def apply_receipts(self, *args, **kwargs):
        self._healthy()
        return super().apply_receipts(*args, **kwargs)

    def add_store_candidate(self, metadata):
        self._healthy()
        return super().add_store_candidate(metadata)

    def on_request_reset(self, request_id):
        self._healthy()
        return super().on_request_reset(request_id)

    def on_request_arrived(self, request_id):
        self._healthy()
        return super().on_request_arrived(request_id)

    def on_request_finished(self, request_id):
        self._healthy()
        return super().on_request_finished(request_id)

    def _release_session(self, request_id):
        self._healthy()
        return super()._release_session(request_id)

    def commit_protection(self, helper, scheduled_tokens):
        self._healthy()
        if (not isinstance(helper, GenerationBoundedProtection)
                or helper.manager is not self or helper.policy is not self.manager._policy):
            raise ValueError('Protection helper must bind this exact bridge')
        if not scheduled_tokens:
            return helper.commit(0)
        planned = []
        for rid, prepared in helper.prepared.items():
            original = operation_key(prepared.pending.store_metadata)
            prefix = operation_key(prepared.metadata)
            suffix = operation_key(prepared.continuation.store_metadata) if prepared.continuation else None
            native = self.manager._policy._pending.get(rid, [])
            ticket = self.pending.get(original)
            meta = prepared.pending.store_metadata
            ids = meta.op.block_ids[0]
            count = len(prepared.blocks)
            expected_prefix = replace(meta, op=replace(meta.op, end=meta.op.start + count * 16,
                block_ids=[ids[:count]]))
            expected_suffix = (replace(meta, op=replace(meta.op, start=meta.op.start + count * 16,
                block_ids=[ids[count:]])) if count < len(ids) else None)
            if (not native or native[0] is not prepared.pending or ticket is None
                    or ticket.wire != original[0] or rid in self.inflight
                    or self.manager._requests.has_in_flight(rid)
                    or prefix[0] != original[0] or prefix[1] != original[1]
                    or not original[1] < prefix[2] <= original[2]
                    or suffix != ((original[0], prefix[2], original[2]) if prefix[2] < original[2] else None)
                    or prepared.metadata != expected_prefix
                    or (prepared.continuation.store_metadata if prepared.continuation else None) != expected_suffix
                    or (prepared.continuation is not None and prepared.continuation.block_hashes != {
                        bid: prepared.pending.block_hashes[bid] for bid in ids[count:]})
                    or [b.block_id for b in prepared.blocks] != ids[:count]
                    or (suffix is not None and suffix in self.pending)):
                raise ValueError('Protection partition differs from original pending reservation')
            generation = self.ledger.generations[ticket.wire]
            if generation.operations.get(ticket) != 'pending' or generation.error is not None:
                raise RuntimeError('Protection original reservation is not pending')
            planned.append((rid, original, prefix, suffix, ticket))
        if self.ledger.serial + sum(s is not None for _, _, _, s, _ in planned) > self.ledger.operation_capacity:
            raise RuntimeError('Ledger operation lifetime capacity exhausted')
        # Record lineage before native register/policy mutation. Exception paths
        # retain all reservations and block END, including after partial commit.
        try:
            for rid, original, prefix, suffix, ticket in planned:
                del self.pending[original]
                self.pending[prefix] = ticket
                child = self.ledger.derive_suffix(ticket) if suffix else None
                if child:
                    self.pending[suffix] = child
                self.splits.append(dict(original=original, prefix=prefix, suffix=suffix,
                                        parent=ticket, child=child))
            stores = helper.commit(scheduled_tokens)
            if [operation_key(m) for m in stores] != [p for _, _, p, _, _ in planned]:
                raise RuntimeError('Native protection outputs differ from registered partition')
            for rid, original, prefix, suffix, ticket in planned:
                if not self.manager._requests.has_in_flight(rid):
                    raise RuntimeError('Native protection did not register batch')
                self.pending.pop(prefix)
                self.ledger.submit_store(ticket)
                self.batch_serial += 1
                self.inflight[rid] = (f'{ticket.wire}:ledger:{self.batch_serial}', (ticket,))
            self._drop_removed()
            return stores
        except BaseException as exc:
            self.poisoned = repr(exc)
            for _, _, _, _, ticket in planned:
                self.ledger.generations[ticket.wire].error = self.poisoned
            raise
