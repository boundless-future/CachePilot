"""Opt-in original-entry writer leases over real L1 allocation methods.

Not wired into StorageManager. All writes in this harness require the new
API; a writer timeout cannot publish data, free a buffer or accept a new writer.
"""
from dataclasses import dataclass
import uuid

from cachepilot_reservation_native import PinStatus, ReservationLock, ReleaseStatus
from leased_l1_contract import LeasedL1Harness
from lmcache.v1.distributed.error import L1Error


@dataclass(frozen=True)
class WriterHandle:
    manager: str
    sequence: int
    key: object


class _WriterLock:
    def __init__(self, ttl_ms):
        self.core = ReservationLock(ttl_ms)
        self.token, = self.core.acquire()
        status, self.pin = self.core.pin(self.token)
        if status != PinStatus.PINNED:
            raise RuntimeError("Cannot pin new writer")
        self.authorized = False
        self.done = False

    def is_locked(self):
        return self.core.is_locked()

    def lock(self):
        raise RuntimeError("Anonymous writer acquisition forbidden")

    def unlock(self):
        if not self.authorized or self.done:
            raise RuntimeError("Original writer terminal identity required")
        if self.core.unpin(self.pin) != ReleaseStatus.RELEASED:
            raise RuntimeError("Writer unpin failed")
        result = self.core.release(self.token)
        if result not in (ReleaseStatus.RELEASED, ReleaseStatus.STALE_EPOCH):
            raise RuntimeError("Writer disposition failed")
        self.done = True


class OwnedWriterHarness(LeasedL1Harness):
    def __init__(self, *args, writer_ttl_ms=600000, **kwargs):
        super().__init__(*args, **kwargs)
        self.writer_ttl_ms = writer_ttl_ms
        self.writer_identity = uuid.uuid4().hex
        self.writer_sequence = 0
        self.writers = {}

    def reserve_write(self, *args, **kwargs):
        raise RuntimeError("Use reserve_writer with an explicit owner")

    def finish_write(self, *args, **kwargs):
        raise RuntimeError("Use finish_writer with stopped-consumer evidence")

    def finish_write_and_reserve_read_owned(self, *args, **kwargs):
        raise RuntimeError("Writer-to-reader handoff requires an explicit writer token")

    def reserve_writer(self, key, temporary, layout):
        with self._lock:
            result = super().reserve_write([key], [temporary], layout)
            status, buffer = result[key]
            if status != L1Error.SUCCESS:
                return status, None, None
            entry = self._objects[key]
            # Keep the real write lock if guard construction fails. No buffer
            # is published and its allocation outcome remains quarantined.
            lock = _WriterLock(self.writer_ttl_ms)
            self.writer_sequence += 1
            handle = WriterHandle(self.writer_identity, self.writer_sequence, key)
            self.writers[handle] = dict(entry=entry, buffer=buffer, lock=lock,
                                        terminal=False, error=None)
            entry.write_lock = lock
            return status, handle, buffer

    def finish_writer(self, handle, *, succeeded, terminal):
        if type(succeeded) is not bool or terminal is not True:
            raise ValueError("Explicit writer outcome and consumer terminal required")
        with self._lock:
            state = self.writers.get(handle)
            if state is None or state['terminal'] or state['error']:
                return False
            entry = state['entry']
            if (self._objects.get(handle.key) is not entry or entry.memory_obj is not state['buffer']
                    or entry.write_lock is not state['lock']):
                state['error'] = "Original writer entry/buffer changed"
                return False
            state['terminal'] = True
            state['lock'].authorized = True
            try:
                if succeeded:
                    # Real L1 finish_write publishes only after terminal proof.
                    result = super().finish_write([handle.key])
                    if result[handle.key] != L1Error.SUCCESS:
                        raise RuntimeError("Write publication failed")
                else:
                    state['lock'].unlock()
                    result = super().delete([handle.key])
                    if result[handle.key] != L1Error.SUCCESS:
                        raise RuntimeError("Failed writer cleanup unresolved")
            except Exception as exc:
                state['error'] = repr(exc)
                return False
            finally:
                state['lock'].authorized = False
            del self.writers[handle]
            return True
