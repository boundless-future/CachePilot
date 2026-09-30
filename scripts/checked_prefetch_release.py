"""Explicit per-key release results for the opt-in pinned reclaim candidate.

Calls the native L1 API and preserves StorageManager's completion event. It
does not patch installed files or other callers. This exposes partial failure;
it does NOT add reservation identity or make release after TTL safe.
"""
from dataclasses import dataclass
import hashlib
import inspect
from pathlib import Path


@dataclass(frozen=True)
class ReleaseOutcome:
    succeeded: tuple
    failed: tuple
    errors: tuple
    notification_error: str | None = None

    @property
    def complete(self):
        return not self.failed and self.notification_error is None


def check_release_source():
    from lmcache.v1.distributed.l1_manager import L1Manager
    from lmcache.v1.distributed.storage_manager import StorageManager
    expected = {
        L1Manager: "ef9281d51dffc7b9de454d198723845fceb7745918935f738b42a7b1e33a79ad",
        StorageManager: "628136dc664be9f32efc6cc4bb29e62c5e7a7702b8c1a708a1ac14b1fde8a430",
    }
    for cls, digest in expected.items():
        actual = hashlib.sha256(Path(inspect.getsourcefile(cls)).read_bytes()).hexdigest()
        if actual != digest:
            raise RuntimeError(f"Unsupported release source: {cls.__name__}: {actual}")


def release_prefetched_checked(storage, keys, *, read_locks=1):
    from lmcache.v1.distributed.error import L1Error
    from lmcache.v1.mp_observability.event import Event, EventType

    keys = tuple(keys)
    if len(set(keys)) != len(keys) or type(read_locks) is not int or read_locks <= 0:
        raise ValueError("Unique keys and positive reader count required")
    if not keys:
        return ReleaseOutcome((), (), ())
    # Exceptions here may follow partial mutation: caller must retain the job
    # as uncertain and must never automatically retry the whole key set.
    result = storage._l1_manager.finish_read(list(keys), read_locks=read_locks)
    if not isinstance(result, dict) or set(result) != set(keys):
        raise RuntimeError("Incomplete/invalid L1 release result; ownership uncertain")
    succeeded = tuple(k for k in keys if result[k] == L1Error.SUCCESS)
    failed = tuple(k for k in keys if result[k] != L1Error.SUCCESS)
    errors = tuple((str(k), str(result[k])) for k in failed)
    notification_error = None
    try:
        storage._event_bus.publish(Event(
            event_type=EventType.SM_READ_PREFETCHED_FINISHED,
            metadata={"succeeded_keys": list(succeeded), "failed_keys": list(failed)},
        ))
    except Exception as exc:
        # The unlock already happened. Preserve its result even if telemetry
        # fails so that no caller retries a successful release.
        notification_error = repr(exc)
    return ReleaseOutcome(succeeded, failed, errors, notification_error)
