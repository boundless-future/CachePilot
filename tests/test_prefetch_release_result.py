"""Characterization of real release APIs; not a reclamation fix.

Native locks and both installed manager methods run; only allocator/event
delivery are mocked. This keeps partial failure visible as a regression case.
"""
import threading
import unittest
from unittest.mock import Mock

try:
    from lmcache.lmcache_native import TTLLock
    from lmcache.v1.distributed.l1_manager import L1Manager, L1ObjectState
    from lmcache.v1.distributed.storage_manager import StorageManager
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    L1Manager = None


@unittest.skipUnless(L1Manager, "Needs native LMCache storage interfaces")
class PrefetchReleaseResultTests(unittest.TestCase):
    def setUp(self):
        self.manager = L1Manager.__new__(L1Manager)
        self.manager._lock = threading.Lock()
        self.manager._memory_manager = Mock()
        self.manager._registered_listeners = []
        self.manager._event_bus = Mock()
        good, blocked = TTLLock(300), TTLLock(300)
        good.lock()
        blocked.lock()
        writer = TTLLock(300)
        writer.lock()
        self.manager._objects = {
            "good": L1ObjectState(Mock(), TTLLock(300), good, False),
            "blocked": L1ObjectState(Mock(), writer, blocked, False),
        }

    def test_l1_returns_per_key_partial_result_and_preserves_failed_lock(self):
        result = self.manager.finish_read(["good", "blocked", "missing"])
        self.assertEqual(result, {"good": L1Error.SUCCESS,
            "blocked": L1Error.KEY_IN_WRONG_STATE, "missing": L1Error.KEY_NOT_EXIST})
        self.assertFalse(self.manager._objects["good"].read_lock.is_locked())
        self.assertTrue(self.manager._objects["blocked"].read_lock.is_locked())

    def test_storage_manager_hides_partial_result_in_return_value(self):
        storage = StorageManager.__new__(StorageManager)
        storage._l1_manager = self.manager
        storage._event_bus = Mock()
        result = storage.finish_read_prefetched(["good", "blocked", "missing"])
        self.assertIsNone(result)
        event = storage._event_bus.publish.call_args.args[0]
        self.assertEqual(event.metadata["succeeded_keys"], ["good"])
        self.assertEqual(event.metadata["failed_keys"], ["blocked", "missing"])
        self.assertTrue(self.manager._objects["blocked"].read_lock.is_locked())
