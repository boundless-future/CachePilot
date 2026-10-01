"""Real L1 methods and native pins, with observable CPU buffer reuse."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from leased_l1_contract import LeasedL1Harness, LeaseHandle
    from reservation_l1_contract import ReadReservation
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    LeasedL1Harness = None


class CPUBuffer:
    def __init__(self):
        self.data = bytearray(64)

    def get_size(self):
        return len(self.data)

    def get_shapes(self):
        return [(64,)]

    def get_dtypes(self):
        return ["uint8"]


class ReusingAllocator:
    def __init__(self):
        self.pool = []
        self.freed = []
        self.free_error = None

    def get_backend_type(self, obj):
        return "cpu"

    def allocate(self, layout, count):
        objects = [self.pool.pop() if self.pool else CPUBuffer() for _ in range(count)]
        for obj in objects:
            obj.data[:] = b"N" * 64
        return L1Error.SUCCESS, objects

    def free(self, objects):
        for obj in objects:
            self.freed.append(obj)
            obj.data[:] = b"F" * 64
            self.pool.append(obj)
        if objects and self.free_error:
            raise self.free_error


@unittest.skipUnless(LeasedL1Harness, "Needs native extension and pinned LMCache")
class LeasedL1Tests(unittest.TestCase):
    def setUp(self):
        self.allocator = ReusingAllocator()
        self.events = Mock()
        self.manager = LeasedL1Harness(self.allocator, self.events)

    def acquire(self, key="k", *, temporary=False, readers=1):
        result = self.manager.reserve_write([key], [temporary], Mock())
        self.assertEqual(result[key][0], L1Error.SUCCESS)
        self.manager._objects[key].memory_obj.data[:] = b"K" * 64
        acquired = self.manager.finish_write_and_reserve_read_owned([key], readers=readers)
        self.assertIsNone(acquired.error)
        return acquired.reservations

    def expire(self):
        time.sleep(0.06)

    def test_ttl_preserves_bytes_against_delete_clear_write_and_eviction(self):
        self.manager._reservation_ttl_ms = 30
        token, = self.acquire()
        lease = self.manager.begin_read_owned([token])
        entry = self.manager._objects["k"]
        self.assertIs(lease.buffers[0], entry.memory_obj)
        self.expire()
        self.assertEqual(entry.read_lock.core.live_count(), 0)
        self.assertFalse(self.manager.is_key_evictable("k"))
        self.assertEqual(self.manager.delete(["k"])["k"], L1Error.KEY_IS_LOCKED)
        self.manager.clear()
        self.assertIn("k", self.manager._objects)
        self.assertNotEqual(self.manager.reserve_write(["k"], [False], Mock())["k"][0],
                            L1Error.SUCCESS)
        self.assertEqual(bytes(lease.buffers[0].data), b"K" * 64)
        self.assertFalse(self.allocator.freed)
        self.assertTrue(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertTrue(self.manager.is_key_evictable("k"))
        self.assertEqual(self.manager.delete(["k"])["k"], L1Error.SUCCESS)
        self.assertEqual(bytes(lease.buffers[0].data), b"F" * 64)
        new, = self.acquire()
        self.assertIs(self.manager._objects["k"].memory_obj, lease.buffers[0])
        old = self.manager.begin_read_owned([token])
        self.assertFalse(old.buffers)
        self.assertIn("foreign_lock", old.error)
        self.assertEqual(self.manager.finish_read_owned([new])[0].status, "released")

    def test_early_release_cannot_terminate_active_buffer(self):
        token, = self.acquire(temporary=True)
        lease = self.manager.begin_read_owned([token])
        self.assertEqual(self.manager.finish_read_owned([token])[0].status, "active_lease")
        self.assertTrue(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertIn("k", self.manager._objects)  # Original reservation is still live.
        self.assertEqual(self.manager.finish_read_owned([token])[0].status, "released")
        self.assertEqual(len(self.allocator.freed), 1)

    def test_multiple_expired_temporary_readers_wait_for_last_lease(self):
        self.manager._reservation_ttl_ms = 30
        a, b = self.acquire(temporary=True, readers=2)
        first = self.manager.begin_read_owned([a])
        second = self.manager.begin_read_owned([b])
        self.expire()
        self.assertTrue(self.manager.finish_read_lease(first.handle, terminal=True))
        self.assertIn("k", self.manager._objects)
        self.assertFalse(self.allocator.freed)
        self.assertEqual(bytes(second.buffers[0].data), b"K" * 64)
        self.assertTrue(self.manager.finish_read_lease(second.handle, terminal=True))
        self.assertNotIn("k", self.manager._objects)
        self.assertEqual(len(self.allocator.freed), 1)

    def test_reset_preserves_buffer_and_old_terminal_isolates_new_lease(self):
        old, = self.acquire()
        first = self.manager.begin_read_owned([old])
        core = self.manager._objects["k"].read_lock.core
        core.reset()
        new, = self.manager.reserve_read_owned(["k"]).reservations
        second = self.manager.begin_read_owned([new])
        self.assertTrue(self.manager.finish_read_lease(first.handle, terminal=True))
        self.assertFalse(self.manager.finish_read_lease(first.handle, terminal=True))
        self.assertEqual(core.active_count(), 1)
        self.assertEqual(bytes(second.buffers[0].data), b"K" * 64)
        self.assertEqual(self.manager.finish_read_owned([old])[0].status, "stale_epoch")
        self.assertTrue(self.manager.finish_read_lease(second.handle, terminal=True))

    def test_failed_batch_rolls_back_without_exposing_partial_buffers(self):
        token, = self.acquire()
        missing = ReadReservation("missing", token.token)
        result = self.manager.begin_read_owned([token, missing])
        self.assertIsNone(result.handle)
        self.assertFalse(result.buffers)
        self.assertIsNotNone(result.error)
        self.assertFalse(self.manager.leases)
        self.assertEqual(self.manager._objects["k"].read_lock.core.active_count(), 0)
        self.assertEqual(self.manager._objects["k"].read_lock.core.live_count(), 1)

    def test_stale_inactive_foreign_and_write_locked_tokens_are_rejected(self):
        a, = self.acquire("a")
        b, = self.acquire("b")
        self.assertIn("foreign_lock", self.manager.begin_read_owned(
            [ReadReservation("b", a.token)]).error)
        self.manager.finish_read_owned([a])
        self.assertIn("inactive", self.manager.begin_read_owned([a]).error)
        self.manager._objects["b"].read_lock.core.reset()
        self.assertIn("stale_epoch", self.manager.begin_read_owned([b]).error)
        new, = self.manager.reserve_read_owned(["b"]).reservations
        self.manager._objects["b"].write_lock.lock()
        self.assertIn("write locked", self.manager.begin_read_owned([new]).error)
        self.assertFalse(self.manager.leases)

    def test_ttl_before_begin_cannot_return_expired_buffer(self):
        self.manager._reservation_ttl_ms = 30
        token, = self.acquire()
        self.expire()
        result = self.manager.begin_read_owned([token])
        self.assertFalse(result.buffers)
        self.assertIn("stale_epoch", result.error)

    def test_malformed_batches_are_rejected_before_pin(self):
        token, = self.acquire()
        for batch in ([], [token, token], [token, ReadReservation("b", object())]):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.manager.begin_read_owned(batch)
        self.assertFalse(self.manager.leases)
        self.assertEqual(self.manager._objects["k"].read_lock.core.active_count(), 0)

    def test_terminal_confirmation_and_manager_identity_are_required(self):
        token, = self.acquire()
        lease = self.manager.begin_read_owned([token])
        for terminal in (False, None, 1):
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                self.manager.finish_read_lease(lease.handle, terminal=terminal)
        for handle in (None, LeaseHandle("foreign", lease.handle.sequence)):
            self.assertFalse(self.manager.finish_read_lease(handle, terminal=True))
        self.assertEqual(self.manager._objects["k"].read_lock.core.active_count(), 1)
        self.assertTrue(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertFalse(self.manager.finish_read_lease(lease.handle, terminal=True))

    def test_force_reclaim_and_shutdown_are_explicitly_rejected(self):
        token, = self.acquire()
        lease = self.manager.begin_read_owned([token])
        for operation in (lambda: self.manager.delete(["k"], force=True),
                          lambda: self.manager.clear(force=True), self.manager.close):
            with self.subTest(operation=operation), self.assertRaises(RuntimeError):
                operation()
        self.assertEqual(bytes(lease.buffers[0].data), b"K" * 64)
        self.assertEqual(self.manager._objects["k"].read_lock.core.active_count(), 1)

    def test_cleanup_free_failure_retains_terminal_evidence_without_retry(self):
        self.manager._reservation_ttl_ms = 30
        token, = self.acquire(temporary=True)
        lease = self.manager.begin_read_owned([token])
        self.expire()
        self.allocator.free_error = RuntimeError("free outcome uncertain")
        self.assertFalse(self.manager.finish_read_lease(lease.handle, terminal=True))
        state = self.manager.leases[lease.handle.sequence]
        self.assertTrue(state.terminal)
        self.assertFalse(state.pins)
        self.assertIn("free outcome uncertain", state.error)
        self.assertEqual(state.results[0][1], "released")
        self.assertFalse(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertEqual(len(self.allocator.freed), 1)

    def test_cleanup_notification_failure_does_not_repeat_free(self):
        self.manager._reservation_ttl_ms = 30
        token, = self.acquire(temporary=True)
        lease = self.manager.begin_read_owned([token])
        self.expire()
        self.events.publish.side_effect = RuntimeError("event failure")
        self.assertFalse(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertIn("event failure", self.manager.leases[lease.handle.sequence].error)
        self.assertFalse(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertEqual(len(self.allocator.freed), 1)

    def test_reentrant_replacement_of_later_batch_key_is_not_deleted(self):
        self.manager._reservation_ttl_ms = 30
        a, = self.acquire("a", temporary=True)
        b, = self.acquire("b", temporary=True)
        lease = self.manager.begin_read_owned([a, b])
        self.expire()
        listener = Mock()
        def replace(keys):
            if keys == ["a"]:
                self.manager.delete(["b"])
                self.manager.reserve_write(["b"], [True], Mock())
                self.manager.finish_write(["b"])
        listener.on_l1_keys_deleted_by_manager.side_effect = replace
        self.manager._registered_listeners.append(listener)
        self.assertTrue(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertIn("b", self.manager._objects)
        self.assertEqual(len(self.allocator.freed), 2)

    def test_begin_is_atomic_with_delete_after_reservation_reset(self):
        for _ in range(50):
            token, = self.acquire()
            core = self.manager._objects["k"].read_lock.core
            barrier = threading.Barrier(2)
            def begin():
                barrier.wait()
                return self.manager.begin_read_owned([token])
            def reclaim():
                barrier.wait()
                core.reset()
                return self.manager.delete(["k"])["k"]
            with ThreadPoolExecutor(2) as pool:
                p, d = pool.submit(begin), pool.submit(reclaim)
                lease, deleted = p.result(), d.result()
            if lease.buffers:
                self.assertEqual(deleted, L1Error.KEY_IS_LOCKED)
                self.assertEqual(bytes(lease.buffers[0].data), b"K" * 64)
                self.assertTrue(self.manager.finish_read_lease(lease.handle, terminal=True))
                self.manager.delete(["k"])
            else:
                self.assertEqual(deleted, L1Error.SUCCESS)
            self.assertFalse(self.manager.leases)

    def test_duplicate_terminal_race_unpins_and_frees_once(self):
        self.manager._reservation_ttl_ms = 30
        token, = self.acquire(temporary=True)
        lease = self.manager.begin_read_owned([token])
        self.expire()
        barrier = threading.Barrier(8)
        def finish(_):
            barrier.wait()
            return self.manager.finish_read_lease(lease.handle, terminal=True)
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(finish, range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(self.allocator.freed), 1)
        self.assertFalse(self.manager.leases)

    def test_rollback_unpin_failure_keeps_unresolved_owner_without_retry(self):
        token, = self.acquire()
        entry = self.manager._objects["k"]
        core = entry.read_lock.core
        proxy = Mock(wraps=core)
        proxy.unpin.side_effect = RuntimeError("unpin outcome unknown")
        entry.read_lock.core = proxy
        result = self.manager.begin_read_owned([token, ReadReservation("missing", token.token)])
        self.assertFalse(result.buffers)
        self.assertIsNotNone(result.handle)
        state = self.manager.leases[result.handle.sequence]
        self.assertEqual(len(state.pins), 1)
        self.assertIn("unpin outcome unknown", state.error)
        self.assertIn("Object missing", state.error)
        self.assertFalse(self.manager.finish_read_lease(result.handle, terminal=True))
        proxy.unpin.assert_called_once()
        self.assertEqual(core.active_count(), 1)
        self.assertEqual(self.manager.delete(["k"])["k"], L1Error.KEY_IS_LOCKED)

    def test_partial_terminal_failure_does_not_repeat_successful_unpin(self):
        a, = self.acquire("a")
        b, = self.acquire("b")
        core_a = self.manager._objects["a"].read_lock.core
        core_b = self.manager._objects["b"].read_lock.core
        proxy_a, proxy_b = Mock(wraps=core_a), Mock(wraps=core_b)
        self.manager._objects["a"].read_lock.core = proxy_a
        self.manager._objects["b"].read_lock.core = proxy_b
        lease = self.manager.begin_read_owned([a, b])
        proxy_b.unpin.side_effect = RuntimeError("second unpin uncertain")
        self.assertFalse(self.manager.finish_read_lease(lease.handle, terminal=True))
        state = self.manager.leases[lease.handle.sequence]
        self.assertEqual(len(state.pins), 1)
        self.assertEqual(state.results[0][1], "released")
        self.assertEqual(core_a.active_count(), 0)
        self.assertEqual(core_b.active_count(), 1)
        self.assertFalse(self.manager.finish_read_lease(lease.handle, terminal=True))
        proxy_a.unpin.assert_called_once()
        proxy_b.unpin.assert_called_once()

    def test_buffer_acquisition_holds_metadata_lock_through_pin(self):
        token, = self.acquire()
        entry = self.manager._objects["k"]
        core = entry.read_lock.core
        entered, proceed, deleting = threading.Event(), threading.Event(), threading.Event()
        proxy = Mock(wraps=core)
        def pin(reservation):
            self.assertTrue(self.manager._lock._is_owned())
            entered.set()
            self.assertTrue(proceed.wait(5))
            return core.pin(reservation)
        proxy.pin.side_effect = pin
        entry.read_lock.core = proxy
        def delete():
            deleting.set()
            return self.manager.delete(["k"])["k"]
        with ThreadPoolExecutor(2) as pool:
            reading = pool.submit(self.manager.begin_read_owned, [token])
            try:
                self.assertTrue(entered.wait(5))
                deletion = pool.submit(delete)
                self.assertTrue(deleting.wait(5))
                self.assertFalse(deletion.done())
            finally:
                proceed.set()
            lease = reading.result()
            self.assertEqual(deletion.result(), L1Error.KEY_IS_LOCKED)
        self.assertIs(lease.buffers[0], entry.memory_obj)
        self.assertTrue(self.manager.finish_read_lease(lease.handle, terminal=True))

    def test_cpu_consumer_finishes_after_ttl_before_terminal_unpin(self):
        self.manager._reservation_ttl_ms = 30
        token, = self.acquire(temporary=True)
        lease = self.manager.begin_read_owned([token])
        start, finish = threading.Event(), threading.Event()
        def consume():
            start.set()
            if not finish.wait(5):
                raise AssertionError("Consumer was not released")
            return bytes(lease.buffers[0].data)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(consume)
            try:
                self.assertTrue(start.wait(5))
                self.expire()
                self.manager.clear()
                self.assertEqual(self.manager.delete(["k"])["k"], L1Error.KEY_IS_LOCKED)
                self.assertFalse(future.done())
                self.assertEqual(self.manager._objects["k"].read_lock.core.active_count(), 1)
            finally:
                finish.set()
            self.assertEqual(future.result(), b"K" * 64)
        self.assertTrue(self.manager.finish_read_lease(lease.handle, terminal=True))
        self.assertNotIn("k", self.manager._objects)
        self.assertEqual(len(self.allocator.freed), 1)


if __name__ == "__main__":
    unittest.main()
