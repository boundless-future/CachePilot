"""CPU buffer leases over real L1 methods; no service or DMA integration.

The caller must stop using every returned buffer BEFORE terminal=True.
After buffer delivery, exceptions, cancellation intent, timeout and garbage
collection never unpin. Failed acquisition rolls back unpublished pins.
"""
from dataclasses import dataclass, field
import uuid

from reservation_l1_contract import ReservationL1Harness, ReadReservation, Reservation
from cachepilot_reservation_native import PinStatus, ReleaseStatus
from lmcache.v1.distributed.error import L1Error


@dataclass(frozen=True)
class LeaseHandle:
    manager: str
    sequence: int


@dataclass(frozen=True)
class BufferLease:
    handle: LeaseHandle | None
    buffers: tuple
    error: str | None = None


@dataclass
class LeaseState:
    handle: LeaseHandle
    pins: dict = field(default_factory=dict)
    entries: list = field(default_factory=list)
    results: list = field(default_factory=list)
    terminal: bool = False
    error: str | None = None


class LeasedL1Harness(ReservationL1Harness):
    """Pin valid reservations and acquire original buffers under the L1 lock.

    Actual buffers are supplied by the allocator fixture. Pin/unpin is the
    isolated C++ implementation; normal read/write/delete/clear use real L1
    methods. Force/free and shutdown remain outside this bounded contract.
    """
    def __init__(self, allocator, event_bus, **kwargs):
        super().__init__(allocator, event_bus, **kwargs)
        self.leases = {}
        self._lease_manager = uuid.uuid4().hex
        self._lease_sequence = 0

    def _lease(self, handle):
        if not isinstance(handle, LeaseHandle):
            return None
        state = self.leases.get(handle.sequence)
        return state if state is not None and state.handle == handle else None

    def begin_read_owned(self, reservations):
        reservations = tuple(reservations)
        if (not reservations
                or any(not isinstance(r, ReadReservation) or not isinstance(r.token, Reservation)
                       for r in reservations)
                or len({r.key for r in reservations}) != len(reservations)):
            raise ValueError("Nonempty batch of captured reservations with unique keys required")
        with self._lock:
            handle = LeaseHandle(self._lease_manager, self._lease_sequence)
            self._lease_sequence += 1
            state = LeaseState(handle)
            self.leases[handle.sequence] = state
            buffers = []
            try:
                for reservation in reservations:
                    entry = self._objects.get(reservation.key)
                    if entry is None or not entry.available_for_read():
                        raise RuntimeError("Object missing or write locked")
                    status, pin = entry.read_lock.core.pin(reservation.token)
                    if status != PinStatus.PINNED:
                        raise RuntimeError("Invalid reservation: " + status.name.lower())
                    try:
                        state.pins[(pin.lock_id, pin.serial)] = (entry.read_lock.core, pin)
                    except BaseException:
                        entry.read_lock.core.unpin(pin)
                        raise
                    state.entries.append((reservation.key, entry))
                    buffers.append(entry.memory_obj)
                # The lock stays held until every validated buffer is pinned.
                return BufferLease(handle, tuple(buffers))
            except Exception as exc:
                state.error = repr(exc)
                self._unpin(state)
                if not state.pins:
                    del self.leases[handle.sequence]
                    return BufferLease(None, (), state.error)
                return BufferLease(handle, (), state.error)

    def _unpin(self, state):
        for identity, (core, pin) in tuple(state.pins.items()):
            try:
                result = core.unpin(pin)
                if result == ReleaseStatus.RELEASED:
                    del state.pins[identity]
                else:
                    error = "Incomplete lease release: " + result.name.lower()
                    state.error = "; ".join(filter(None, (state.error, error)))
                # Commit known unpins before potentially failing bookkeeping.
                state.results.append((identity, result.name.lower()))
            except Exception as exc:
                state.error = "; ".join(filter(None, (state.error, repr(exc))))

    def finish_read_lease(self, handle, *, terminal):
        if terminal is not True:
            raise ValueError("Consumer must explicitly confirm it stopped using buffers")
        with self._lock:
            state = self._lease(handle)
            if state is None or state.terminal or state.error:
                return False
            state.terminal = True
            self._unpin(state)
            if state.pins or state.error:
                return False
            try:
                # TTL may have expired while pinned. Delete only the captured
                # temporary object, never a reentrant replacement of its key.
                for key, entry in state.entries:
                    if (self._objects.get(key) is entry and entry.is_temporary
                            and not entry.read_lock.is_locked() and not entry.write_lock.is_locked()):
                        result = super().delete([key])
                        if result[key] != L1Error.SUCCESS:
                            raise RuntimeError("Temporary cleanup failed: " + str(result[key]))
            except Exception as exc:
                state.error = repr(exc)
                return False
            del self.leases[handle.sequence]
            return True

    def delete(self, keys, force=False):
        if force:
            raise RuntimeError("Forced deletion is outside the buffer lease contract")
        return super().delete(keys)

    def clear(self, force=False):
        if force:
            raise RuntimeError("Forced clear is outside the buffer lease contract")
        return super().clear()

    def close(self):
        raise RuntimeError("Shutdown must drain actual readers/writers before allocator close")
