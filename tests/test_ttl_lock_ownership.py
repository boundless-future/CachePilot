"""Characterize why request timeout alone cannot authorize anonymous unlocks.

Real native TTLLock and L1Manager.finish_read; memory allocation is mocked.
This test records a limitation, not proof of a fixed ownership protocol.
"""
import threading
import time
import unittest
from unittest.mock import Mock

try:
    from lmcache.lmcache_native import TTLLock
    from lmcache.v1.distributed.l1_manager import L1Manager, L1ObjectState
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    L1Manager = None


@unittest.skipUnless(L1Manager, "Needs pinned native LMCache environment")
class TTLOwnershipTests(unittest.TestCase):
    def test_late_anonymous_unlock_can_consume_a_new_reservation(self):
        lock = TTLLock(1)
        lock.lock()  # Old request's reservation.
        deadline = time.monotonic() + 3
        while lock.is_locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(lock.is_locked())
        lock.lock()  # New request acquires the same key; expired count resets.
        self.assertTrue(lock.is_locked())
        manager = L1Manager.__new__(L1Manager)
        manager._lock = threading.Lock()
        manager._objects = {"same-key": L1ObjectState(Mock(), TTLLock(1), lock, False)}
        manager._memory_manager = Mock()
        manager._registered_listeners = []
        manager._event_bus = Mock()
        # API has key and count only; a stale caller can release the new lock.
        result = manager.finish_read(["same-key"], read_locks=1)
        self.assertEqual(result["same-key"], L1Error.SUCCESS)
        self.assertFalse(lock.is_locked())


if __name__ == "__main__":
    unittest.main()
