"""Real native lock outcomes fed into the reclaim candidate, without CUDA."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from checked_prefetch_release import check_release_source, release_prefetched_checked
from lookup_server_reclaim import Abandoned, ReclaimState

try:
    from lmcache.lmcache_native import TTLLock, Bitmap
    from lmcache.v1.distributed.l1_manager import L1Manager, L1ObjectState
    from lmcache.v1.distributed.storage_manager import StorageManager
except ModuleNotFoundError:
    L1Manager = None


@unittest.skipUnless(L1Manager, "Needs pinned native LMCache")
class CheckedPrefetchReleaseTests(unittest.TestCase):
    def setUp(self):
        self.l1 = L1Manager.__new__(L1Manager)
        self.l1._lock = threading.Lock()
        self.l1._memory_manager = Mock()
        self.l1._registered_listeners = []
        self.l1._event_bus = Mock()
        self.l1._objects = {}
        for key in ("a", "b"):
            reader = TTLLock(300)
            reader.lock()
            self.l1._objects[key] = L1ObjectState(Mock(), TTLLock(300), reader, False)
        self.storage = StorageManager.__new__(StorageManager)
        self.storage._l1_manager = self.l1
        self.storage._event_bus = Mock()
        self.release = Mock(wraps=self.l1.finish_read)
        self.l1.finish_read = self.release
        module = SimpleNamespace(_ctx=SimpleNamespace(storage_manager=self.storage))
        self.state = ReclaimState(module, background=False, checked_release=True)

    def abandon(self, keys=("a", "b"), ready=True):
        job = SimpleNamespace(request_id="old", handle=object())
        found = Bitmap(len(keys))
        found.batched_set(range(len(keys)))
        self.entry = Abandoned(job, keys, 1)
        self.state.abandoned[1] = self.entry
        self.storage.query_prefetch_status = Mock(return_value=found if ready else None)
        return found

    def test_pinned_native_source_is_supported(self):
        check_release_source()

    def test_success_releases_once_and_preserves_original_storage_event(self):
        self.abandon()
        self.state.reap_once()
        self.state.reap_once()
        self.assertEqual(self.state.completed, 1)
        self.assertFalse(self.state.abandoned)
        self.release.assert_called_once_with(["a", "b"], read_locks=1)
        event = self.storage._event_bus.publish.call_args.args[0]
        self.assertEqual(event.metadata, dict(succeeded_keys=["a", "b"], failed_keys=[]))

    def test_pending_io_never_releases_then_terminal_result_does(self):
        found = self.abandon(ready=False)
        self.state.reap_once()
        self.release.assert_not_called()
        self.storage.query_prefetch_status.return_value = found
        self.state.reap_once()
        self.assertFalse(self.state.abandoned)

    def test_partial_failure_retained_and_success_is_not_retried(self):
        self.l1._objects["b"].write_lock.lock()
        self.abandon(("a", "b", "missing"))
        with self.assertLogs("cachepilot.lookup_reclaim", "ERROR"):
            self.state.reap_once()
        self.assertEqual(self.state.completed, 0)
        self.assertEqual(self.entry.release_outcome.succeeded, ("a",))
        self.assertEqual(self.entry.release_outcome.failed, ("b", "missing"))
        self.assertFalse(self.l1._objects["a"].read_lock.is_locked())
        # A fresh reader arrives after the successful release. Sweeping again
        # must not decrement that reader while retrying the failed keys.
        self.l1._objects["a"].read_lock.lock()
        self.state.reap_once()
        self.release.assert_called_once()
        self.assertTrue(self.l1._objects["a"].read_lock.is_locked())
        self.assertTrue(self.l1._objects["b"].read_lock.is_locked())

    def test_notification_failure_keeps_known_success_without_unlock_retry(self):
        self.storage._event_bus.publish.side_effect = RuntimeError("event sink unavailable")
        self.abandon()
        with self.assertLogs("cachepilot.lookup_reclaim", "ERROR"):
            self.state.reap_once()
        self.assertEqual(self.entry.release_outcome.succeeded, ("a", "b"))
        self.assertIsNotNone(self.entry.release_outcome.notification_error)
        self.state.reap_once()
        self.release.assert_called_once()
        self.assertEqual(self.state.completed, 0)

    def test_exception_after_partial_mutation_is_uncertain_and_not_retried(self):
        def partially_mutate(*args, **kwargs):
            L1Manager.finish_read(self.l1, ["a"])
            raise RuntimeError("native caller failed after one key")
        self.release.side_effect = partially_mutate
        self.abandon()
        with self.assertLogs("cachepilot.lookup_reclaim", "ERROR"):
            self.state.reap_once()
        self.state.reap_once()
        self.assertIsNone(self.entry.release_outcome)
        self.assertIsNotNone(self.entry.error)
        self.release.assert_called_once()
        self.assertFalse(self.l1._objects["a"].read_lock.is_locked())
        self.assertTrue(self.l1._objects["b"].read_lock.is_locked())

    def test_duplicate_keys_and_invalid_counts_rejected_before_mutation(self):
        for keys, count in [(["a", "a"], 1), (["a"], 0), (["a"], True)]:
            with self.assertRaises(ValueError):
                release_prefetched_checked(self.storage, keys, read_locks=count)
        self.release.assert_not_called()

    def test_incomplete_result_never_marks_complete(self):
        self.release.return_value = {"a": 0}
        self.abandon()
        with self.assertLogs("cachepilot.lookup_reclaim", "ERROR"):
            self.state.reap_once()
        self.state.reap_once()
        self.assertEqual(self.state.completed, 0)
        self.release.assert_called_once()

    def test_empty_completed_bitmap_needs_no_release(self):
        self.abandon(())
        self.state.reap_once()
        self.release.assert_not_called()
        self.assertEqual(self.state.completed, 1)
