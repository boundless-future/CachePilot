"""Real L1Manager reserve/write/delete + experimental C++ read lock, no GPU."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from reservation_l1_contract import ReservationL1Harness, ReadReservation
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    ReservationL1Harness = None


@unittest.skipUnless(ReservationL1Harness, "Needs native extension and pinned LMCache")
class ReservationL1Tests(unittest.TestCase):
    def setUp(self):
        self.allocator = Mock()
        self.allocator.allocate.side_effect = lambda layout, count: (
            L1Error.SUCCESS, [Mock(name=f"memory-{i}") for i in range(count)])
        self.events = Mock()
        self.manager = ReservationL1Harness(self.allocator, self.events)

    def create(self, key="k", temporary=False):
        result = self.manager.reserve_write([key], [temporary], Mock())
        self.assertEqual(result[key][0], L1Error.SUCCESS)
        return self.manager._objects[key]

    def acquire(self, key="k", temporary=False, readers=1):
        self.create(key, temporary)
        result = self.manager.finish_write_and_reserve_read_owned([key], readers=readers)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.reservations), readers)
        return result.reservations

    def test_write_to_read_retains_identity_until_last_temporary_reader(self):
        first, second = self.acquire(temporary=True, readers=2)
        self.assertEqual(self.manager.finish_read_owned([first])[0].status, "released")
        self.assertIn("k", self.manager._objects)
        self.allocator.free.assert_not_called()
        self.assertEqual(self.manager.finish_read_owned([first])[0].status, "inactive")
        self.assertEqual(self.manager.finish_read_owned([second])[0].status, "released")
        self.assertNotIn("k", self.manager._objects)
        self.allocator.free.assert_called_once()
        self.assertEqual(len(self.allocator.free.call_args.args[0]), 1)

    def test_real_reserve_read_acquires_independent_tokens(self):
        old, = self.acquire()
        result = self.manager.reserve_read_owned(["k", "missing"], readers=2)
        self.assertEqual(result.native_result["missing"][0], L1Error.KEY_NOT_EXIST)
        self.assertEqual(len(result.reservations), 2)
        self.manager.finish_read_owned([old])
        self.manager.finish_read_owned([old])
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 2)
        self.assertTrue(all(r.status == "released" for r in
                            self.manager.finish_read_owned(result.reservations)))

    def test_ttl_then_new_reader_rejects_old_completion(self):
        self.manager._reservation_ttl_ms = 40
        old, = self.acquire(temporary=True)
        time.sleep(0.08)
        new, = self.manager.reserve_read_owned(["k"]).reservations
        result = self.manager.finish_read_owned([old])[0]
        self.assertEqual(result.status, "stale_epoch")
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 1)
        self.allocator.free.assert_not_called()
        self.manager.finish_read_owned([new])
        self.assertNotIn("k", self.manager._objects)

    def test_deleted_and_recreated_key_rejects_old_token(self):
        old, = self.acquire()
        self.manager.finish_read_owned([old])
        self.assertEqual(self.manager.delete(["k"])["k"], L1Error.SUCCESS)
        new, = self.acquire()
        self.assertEqual(self.manager.finish_read_owned([old])[0].status, "foreign_lock")
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 1)
        self.assertEqual(self.manager.finish_read_owned([new])[0].status, "released")

    def test_acquisition_notification_failure_preserves_tokens(self):
        self.create()
        self.events.publish.side_effect = RuntimeError("notification failure")
        acquired = self.manager.finish_write_and_reserve_read_owned(["k"])
        self.assertIsNotNone(acquired.error)
        self.assertEqual(len(acquired.reservations), 1)
        self.events.publish.side_effect = None
        self.assertEqual(self.manager.finish_read_owned(acquired.reservations)[0].status, "released")

    def test_release_notification_failure_cannot_repeat_unlock(self):
        old, new = self.acquire(readers=2)
        self.events.publish.side_effect = RuntimeError("notification failure")
        result = self.manager.finish_read_owned([old])[0]
        self.assertEqual(result.status, "released")
        self.assertIsNotNone(result.error)
        self.events.publish.side_effect = None
        self.assertEqual(self.manager.finish_read_owned([old])[0].status, "inactive")
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 1)
        self.assertEqual(self.manager.finish_read_owned([new])[0].status, "released")

    def test_free_failure_preserves_known_unlock_and_never_retries_free(self):
        token, = self.acquire(temporary=True)
        self.allocator.free.side_effect = RuntimeError("allocator failure")
        result = self.manager.finish_read_owned([token])[0]
        self.assertEqual(result.status, "released")
        self.assertIsNotNone(result.error)
        self.assertEqual(self.manager.finish_read_owned([token])[0].status, "object_gone")
        self.allocator.free.assert_called_once()  # Remains unresolved, not a recovery claim.

    def test_mixed_release_results_keep_new_reader_and_wrong_key_safe(self):
        first, = self.acquire("a")
        second, = self.acquire("b")
        wrong = ReadReservation("b", first.token)
        results = self.manager.finish_read_owned([first, first, wrong, second])
        self.assertEqual([r.status for r in results],
                         ["released", "inactive", "foreign_lock", "released"])

    def test_anonymous_interfaces_and_invalid_inputs_are_rejected(self):
        token, = self.acquire()
        for method in (self.manager.reserve_read, self.manager.finish_read,
                       self.manager.unsafe_read, self.manager.finish_write_and_reserve_read):
            with self.assertRaises(RuntimeError):
                method(["k"])
        for keys, readers in [(["k", "k"], 1), (["k"], 0), (["k"], 129), (["k"], True)]:
            with self.assertRaises(ValueError):
                self.manager.reserve_read_owned(keys, readers=readers)
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 1)
        self.manager.finish_read_owned([token])

    def test_write_locked_object_cannot_acquire_or_consume_read_reservation(self):
        self.create()
        result = self.manager.reserve_read_owned(["k"])
        self.assertEqual(result.native_result["k"][0], L1Error.KEY_NOT_READABLE)
        self.assertFalse(result.reservations)
        acquired = self.manager.finish_write_and_reserve_read_owned(["k"])
        self.manager._objects["k"].write_lock.lock()  # Deliberately invalid mixed state.
        self.assertEqual(self.manager.finish_read_owned(acquired.reservations)[0].status, "write_locked")
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 1)

    def test_malformed_batch_is_rejected_before_any_release(self):
        token, = self.acquire()
        with self.assertRaises(ValueError):
            self.manager.finish_read_owned([token, ReadReservation("k", object())])
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 1)

    def test_reentrant_listener_replacement_is_not_deleted_by_old_release(self):
        token, = self.acquire(temporary=True)
        listener = Mock()
        def replace(keys):
            self.manager.delete(keys)
            self.create(temporary=True)
            self.manager.finish_write(keys)  # New, idle temporary object.
        listener.on_l1_keys_read_finished.side_effect = replace
        self.manager._registered_listeners.append(listener)
        result = self.manager.finish_read_owned([token])[0]
        self.assertEqual(result.status, "released")
        self.assertIsNone(result.error)
        self.assertIn("k", self.manager._objects)
        self.allocator.free.assert_called_once()  # Listener freed only the old object.


if __name__ == "__main__":
    unittest.main()
