"""CPU-only contract harness around pinned, real L1Manager methods.

This is NOT installable in a service. Allocator and event sink must be supplied
explicitly; no GPU memory is allocated here. It exercises the read ownership
interface that StorageManager/controller/wire consumers would have to adopt.
Writer identity, I/O terminal ownership and lease recovery are not implemented.
"""
from dataclasses import dataclass
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "artifacts/reservation-native"))
from cachepilot_reservation_native import Reservation, ReservationLock, ReleaseStatus
from lmcache.v1.distributed.l1_manager import L1Manager
from lmcache.v1.distributed.error import L1Error
from lmcache.v1.mp_observability.event import Event, EventType

from checked_prefetch_release import check_release_source


@dataclass(frozen=True)
class ReadReservation:
    key: object
    token: object


@dataclass(frozen=True)
class Acquisition:
    native_result: dict | None
    reservations: tuple[ReadReservation, ...]
    error: str | None = None


@dataclass(frozen=True)
class Release:
    reservation: ReadReservation
    status: str
    error: str | None = None


class _ReadLock:
    """Capture native reservations during real L1Manager read acquisition."""
    def __init__(self, key, ttl_ms):
        self.key = key
        self.core = ReservationLock(ttl_ms)
        self.capture = None

    def lock(self):
        if self.capture is None:
            raise RuntimeError("Read acquisition requires reservation capture")
        token, = self.core.acquire()
        try:
            self.capture.append(ReadReservation(self.key, token))
        except Exception:
            self.core.release(token)
            raise

    def unlock(self):
        raise RuntimeError("Anonymous release forbidden")

    def is_locked(self):
        return self.core.is_locked()


class ReservationL1Harness(L1Manager):
    def __init__(self, allocator, event_bus, *, read_ttl_ms=300000):
        check_release_source()
        # CPU contract fixture, intentionally not L1Manager.__init__ (which
        # constructs a configured real allocator). No service uses this class.
        self._lock = threading.RLock()
        self._objects = {}
        self._memory_manager = allocator
        self._event_bus = event_bus
        self._registered_listeners = []
        self._write_ttl_seconds = 600
        self._read_ttl_seconds = 300  # Native placeholders replaced below.
        self._reservation_ttl_ms = read_ttl_ms

    def reserve_write(self, *args, **kwargs):
        with self._lock:
            try:
                return super().reserve_write(*args, **kwargs)
            finally:
                # Also cover a notification raising after real allocation.
                for key, entry in self._objects.items():
                    if not isinstance(entry.read_lock, _ReadLock):
                        if entry.read_lock.is_locked():
                            raise RuntimeError("Cannot adopt existing anonymous readers")
                        entry.read_lock = _ReadLock(key, self._reservation_ttl_ms)

    def _acquire(self, method, keys, readers):
        keys = tuple(keys)
        if len(set(keys)) != len(keys) or type(readers) is not int or not 1 <= readers <= 128:
            raise ValueError("Unique keys and reader count in [1, 128] required")
        captured = []
        with self._lock:
            locks = [self._objects[k].read_lock for k in keys if k in self._objects]
            if any(not isinstance(lock, _ReadLock) or lock.capture is not None for lock in locks):
                raise RuntimeError("Unsupported lock or nested reservation acquisition")
            for lock in locks:
                lock.capture = captured
            try:
                result = method(list(keys), read_locks=readers)
                return Acquisition(result, tuple(captured))
            except Exception as exc:
                # Native event/listener failure can follow successful locks.
                # Return the tokens, never silently drop their ownership.
                return Acquisition(None, tuple(captured), repr(exc))
            finally:
                for lock in locks:
                    lock.capture = None

    def reserve_read_owned(self, keys, *, readers=1):
        return self._acquire(super().reserve_read, keys, readers)

    def finish_write_and_reserve_read_owned(self, keys, *, readers=1):
        return self._acquire(super().finish_write_and_reserve_read, keys, readers)

    def finish_read_owned(self, reservations):
        reservations = tuple(reservations)
        for item in reservations:
            if not isinstance(item, ReadReservation) or not isinstance(item.token, Reservation):
                raise ValueError("Release requires captured native reservation tokens")
            hash(item.key)
        results = []
        with self._lock:
            for reservation in reservations:
                entry = self._objects.get(reservation.key)
                if entry is None:
                    results.append(Release(reservation, "object_gone"))
                    continue
                if entry.write_lock.is_locked():
                    results.append(Release(reservation, "write_locked"))
                    continue
                status = entry.read_lock.core.release(reservation.token)
                if status != ReleaseStatus.RELEASED:
                    results.append(Release(reservation, status.name.lower()))
                    continue
                # Unlock has committed. Subsequent failures are bookkeeping or
                # allocator uncertainty, not permission to repeat the unlock.
                errors = []
                try:
                    for listener in self._registered_listeners:
                        listener.on_l1_keys_read_finished([reservation.key])
                    self._event_bus.publish(Event(
                        event_type=EventType.L1_READ_FINISHED,
                        metadata={"keys": [reservation.key]},
                    ))
                except Exception as exc:
                    errors.append(repr(exc))
                if (self._objects.get(reservation.key) is entry
                        and entry.is_temporary and not entry.read_lock.is_locked()):
                    try:
                        result = super().delete([reservation.key])
                        if result[reservation.key] != L1Error.SUCCESS:
                            errors.append(str(result[reservation.key]))
                    except Exception as exc:
                        errors.append(repr(exc))
                results.append(Release(reservation, "released", "; ".join(errors) or None))
        return tuple(results)

    # Reject legacy consumers, including anonymous unsafe reads. Otherwise a
    # service could silently mix the two protocols and invalidate the proof.
    def reserve_read(self, *args, **kwargs):
        raise RuntimeError("Use reserve_read_owned")

    def finish_write_and_reserve_read(self, *args, **kwargs):
        raise RuntimeError("Use finish_write_and_reserve_read_owned")

    def finish_read(self, *args, **kwargs):
        raise RuntimeError("Use finish_read_owned")

    def unsafe_read(self, *args, **kwargs):
        raise RuntimeError("Token-validated data access is not implemented")
