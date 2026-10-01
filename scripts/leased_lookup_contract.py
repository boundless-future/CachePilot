"""CPU Lookup reader slots connected to native/L1 buffer leases, no DMA/wire.

The consumer must stop accessing every delivered buffer before terminal=True.
Only whole-shard, one-shot reads are supported. Cancellation is not terminal.
"""
from dataclasses import dataclass, field

from leased_l1_contract import LeasedL1Harness
from owned_lookup_contract import OwnedLookupHarness, ReaderTicket
from owned_prefetch_contract import token_id
from reservation_l1_contract import Release


@dataclass(frozen=True)
class RetrievalBuffers:
    ticket: ReaderTicket
    buffers: tuple
    error: str | None = None


@dataclass
class ReaderAccess:
    ticket: ReaderTicket
    phase: str = "acquiring"
    lease: object | None = None
    origins: dict = field(default_factory=dict)
    lease_finished: bool = False
    error: str | None = None


class LeasedLookupHarness(OwnedLookupHarness):
    """Keep original tokens, buffers and leases attached to their worker slot.

    gate precedes the L1 metadata lock. Reentrant END marks abandonment and
    advances after scope exit. Unknown outcomes retain both registries.
    """
    def __init__(self, ctx, storage):
        if not isinstance(storage._l1_manager.manager, LeasedL1Harness):
            raise ValueError("A leased L1 manager is required")
        super().__init__(ctx, storage)
        self.l1 = storage._l1_manager.manager
        self.accesses = {}
        self.expired = 0

    def claim_retrieve(self, ticket):
        if not isinstance(ticket, ReaderTicket):
            return None
        return super().claim_retrieve(ticket)

    def read_retrieve(self, ticket):
        if not isinstance(ticket, ReaderTicket):
            return None
        with self.gate:
            job = self._job(ticket.lookup)
            if (job is None or job.error or job.abandoned or job.processing
                    or self._scope is not None):
                return None
            slot = job.slots.get(ticket)
            if slot is None or slot.state != "running" or ticket in self.accesses:
                return None
            access = ReaderAccess(ticket)
            self.accesses[ticket] = access
            job.processing = True
            self._scope = job
            try:
                with self.l1._lock:
                    lease = self.l1.begin_read_owned(slot.reservations)
                    access.lease = lease  # Save before any further validation.
                    access.error = lease.error
                    if lease.handle is not None:
                        state = self.l1._lease(lease.handle)
                        if state is None:
                            raise RuntimeError("L1 lease owner disappeared")
                        entries = dict(state.entries)
                        cores = {identity[0]: core for identity, (core, _) in state.pins.items()}
                        # Capture original native cores, including partial
                        # rollback evidence; never reconstruct from new keys.
                        access.origins = {token_id(r): (entries[r.key], cores[r.token.lock_id])
                            for r in slot.reservations
                            if r.key in entries and r.token.lock_id in cores}
                    if lease.error:
                        access.phase = "unpublished"
                        return RetrievalBuffers(ticket, (), lease.error)
                    if (lease.handle is None or len(lease.buffers) != len(slot.reservations)
                            or len(access.origins) != len(slot.reservations)):
                        raise RuntimeError("Buffer lease does not cover the worker shard")
                    if job.abandoned:
                        access.phase = "unpublished"
                        access.error = "Cancelled before buffer delivery"
                        return RetrievalBuffers(ticket, (), access.error)
                    access.phase = "delivered"
                    return RetrievalBuffers(ticket, lease.buffers)
            except Exception as exc:
                access.error = repr(exc)
                job.error = repr(exc)
                return RetrievalBuffers(ticket, (), access.error)
            finally:
                self._scope = None
                job.processing = False
                if job.abandoned and not job.error:
                    self.advance(job.handle)

    def _release(self, job, reservations):
        reservations = tuple(reservations)
        identities = {token_id(r) for r in reservations}
        origins = {}
        for ticket, access in self.accesses.items():
            if ticket.lookup != job.handle:
                continue
            slot = job.slots[ticket]
            if not identities.intersection(token_id(r) for r in slot.reservations):
                continue
            if access.phase != "terminal" or not access.lease_finished:
                raise RuntimeError("Cannot retire tokens before the buffer lease terminates")
            origins.update(access.origins)
        # Unpin, token disposition and temporary cleanup share this metadata
        # lock when called by finish_retrieve; normal END also enters it.
        with self.l1._lock:
            for reservation in reservations:
                identity = token_id(reservation)
                origin = origins.get(identity)
                if origin is not None and self.l1._objects.get(reservation.key) is not origin[0]:
                    # Expired temporary buffers can already have been deleted
                    # by lease cleanup. Consult their retained original core.
                    status = origin[1].release(reservation.token).name.lower()
                    result = Release(reservation, status, None if status == "stale_epoch" else
                                     "Original object changed without confirmed reservation expiry")
                else:
                    result, = self.l1.finish_read_owned([reservation])
                job.release_results.append(result)
                if result.status in ("released", "stale_epoch"):
                    job.tokens.pop(identity)
                    self.expired += result.status == "stale_epoch"
                if result.status not in ("released", "stale_epoch") or result.error:
                    raise RuntimeError("Incomplete token disposition; retained without retry")

    def finish_retrieve(self, ticket, *, succeeded, terminal=False):
        if type(succeeded) is not bool or terminal is not True:
            raise ValueError("Explicit outcome and confirmation of stopped buffer access required")
        if not isinstance(ticket, ReaderTicket):
            return False
        with self.gate:
            job = self._job(ticket.lookup)
            # A failed sibling must not prevent a stopped consumer from
            # terminating its own lease. Terminal slots still cannot retry.
            if job is None or job.processing or self._scope is not None:
                return False
            slot = job.slots.get(ticket)
            if slot is None or slot.state != "running":
                return False
            access = self.accesses.get(ticket)
            if succeeded and (access is None or access.phase != "delivered"):
                raise ValueError("Successful retrieval requires delivered buffers")
            job.processing = True
            self._scope = job
            try:
                slot.outcome = succeeded
                slot.state = "terminal"
                with self.l1._lock:
                    if access is not None:
                        access.phase = "terminal"
                        if access.lease is None:
                            raise RuntimeError("Unknown L1 acquisition outcome")
                        if access.lease.handle is not None:
                            if not self.l1.finish_read_lease(access.lease.handle, terminal=True):
                                state = self.l1._lease(access.lease.handle)
                                detail = state.error if state is not None else "owner missing"
                                raise RuntimeError("L1 lease termination unresolved: " + str(detail))
                        access.lease_finished = True
                    self._release(job, slot.reservations)
                    slot.state = "closed"
                self.accesses.pop(ticket, None)
            except Exception as exc:
                if access is not None:
                    access.error = repr(exc)
                job.error = "; ".join(filter(None, (job.error, repr(exc))))
                return False
            finally:
                self._scope = None
                job.processing = False
            if job.abandoned:
                self.advance(job.handle)
            self._close_if_drained(job)
            return True
