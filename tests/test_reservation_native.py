"""Real C++ CPU prototype; required on the server, optional on local Windows."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "artifacts/reservation-native"
sys.path.insert(0, str(BUILD))
try:
    from cachepilot_reservation_native import ReservationLock, ReleaseStatus as Status, PinStatus
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    ReservationLock = None


@unittest.skipUnless(ReservationLock, "Build CPU reservation extension first")
class ReservationNativeTests(unittest.TestCase):
    def test_binary_matches_current_source(self):
        manifest = json.loads((BUILD / "build.json").read_text())
        self.assertEqual(manifest["source_sha256"],
                         hashlib.sha256((ROOT / "native/reservation_lock.cpp").read_bytes()).hexdigest())
        self.assertEqual(manifest["binary_sha256"],
                         hashlib.sha256((BUILD / manifest["binary"]).read_bytes()).hexdigest())

    def test_expired_old_token_cannot_release_new_reader(self):
        lock = ReservationLock(40)
        old, = lock.acquire()
        time.sleep(0.08)
        new, = lock.acquire()  # No is_locked poll required to discover expiry.
        self.assertNotEqual(old.epoch, new.epoch)
        self.assertEqual(lock.release(old), Status.STALE_EPOCH)
        self.assertEqual(lock.live_count(), 1)
        self.assertEqual(lock.release(new), Status.RELEASED)

    def test_expired_release_without_new_acquire_is_stale(self):
        lock = ReservationLock(20)
        old, = lock.acquire()
        time.sleep(0.04)
        self.assertEqual(lock.release(old), Status.STALE_EPOCH)
        self.assertEqual(lock.live_count(), 0)

    def test_shared_ttl_refresh_retains_old_reader(self):
        lock = ReservationLock(1000)
        old, = lock.acquire()
        time.sleep(0.6)
        new, = lock.acquire()
        time.sleep(0.6)  # Old age > TTL, but the shared lock deadline was extended.
        self.assertEqual(old.epoch, new.epoch)
        self.assertEqual(lock.release(old), Status.RELEASED)
        self.assertEqual(lock.live_count(), 1)
        self.assertEqual(lock.release(new), Status.RELEASED)

    def test_duplicate_release_does_not_consume_another_reader(self):
        lock = ReservationLock(300000)
        first, second = lock.acquire(2)
        self.assertEqual(lock.release(first), Status.RELEASED)
        self.assertEqual(lock.release(first), Status.INACTIVE)
        self.assertEqual(lock.live_count(), 1)
        self.assertEqual(lock.release(second), Status.RELEASED)
        later, = lock.acquire()
        self.assertNotEqual(later.serial, first.serial)
        self.assertEqual(lock.release(first), Status.INACTIVE)
        self.assertEqual(lock.live_count(), 1)

    def test_reset_and_replaced_lock_reject_old_tokens(self):
        lock = ReservationLock(300000)
        old, = lock.acquire()
        lock.reset()
        new, = lock.acquire()
        self.assertEqual(lock.release(old), Status.STALE_EPOCH)
        replacement = ReservationLock(300000)
        replacement.acquire()
        self.assertEqual(replacement.release(new), Status.FOREIGN_LOCK)
        self.assertEqual(replacement.live_count(), 1)

    def test_multi_reader_reservation_has_independent_tokens(self):
        lock = ReservationLock(300000)
        tokens = lock.acquire(128)
        self.assertEqual(len({t.serial for t in tokens}), 128)
        for token in tokens[::2]:
            self.assertEqual(lock.release(token), Status.RELEASED)
        self.assertEqual(lock.live_count(), 64)
        for token in tokens[1::2]:
            self.assertEqual(lock.release(token), Status.RELEASED)
        self.assertFalse(lock.is_locked())

    def test_invalid_count_and_anonymous_api_fail_before_mutation(self):
        lock = ReservationLock(300000)
        for count in (0, 129):
            with self.assertRaises(ValueError):
                lock.acquire(count)
        token, = lock.acquire()
        for method in (lock.lock, lock.unlock):
            with self.assertRaises(RuntimeError):
                method()
        self.assertEqual(lock.live_count(), 1)
        self.assertEqual(lock.release(token), Status.RELEASED)

    def test_racing_duplicate_release_has_one_winner(self):
        lock = ReservationLock(300000)
        old, new = lock.acquire(2)
        barrier = threading.Barrier(8)
        def release(_):
            barrier.wait()
            return lock.release(old)
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(release, range(8)))
        self.assertEqual(results.count(Status.RELEASED), 1)
        self.assertEqual(results.count(Status.INACTIVE), 7)
        self.assertEqual(lock.live_count(), 1)
        self.assertEqual(lock.release(new), Status.RELEASED)

    def test_concurrent_acquire_release_with_gil_released(self):
        lock = ReservationLock(300000)
        barrier = threading.Barrier(8)
        def worker(_):
            tokens = lock.acquire(64)
            barrier.wait()
            for token in tokens:
                if lock.release(token) != Status.RELEASED:
                    raise AssertionError("Lost reservation")
            return {(t.lock_id, t.epoch, t.serial) for t in tokens}
        with ThreadPoolExecutor(8) as pool:
            sets = list(pool.map(worker, range(8)))
        self.assertEqual(len(set.union(*sets)), 512)
        self.assertEqual(lock.live_count(), 0)

    def test_randomized_reset_and_release_matches_reference(self):
        rng = random.Random(37)
        lock = ReservationLock(300000)
        live, tokens, generation = set(), [], 0
        for _ in range(4000):
            action = rng.randrange(5)
            if action < 2 or not tokens:
                token, = lock.acquire()
                tokens.append((token, generation))
                live.add(token.serial)
            elif action == 2:
                lock.reset()
                live.clear()
                generation += 1
            else:
                token, gen = rng.choice(tokens)
                expected = (Status.STALE_EPOCH if gen != generation else
                            Status.RELEASED if token.serial in live else Status.INACTIVE)
                self.assertEqual(lock.release(token), expected)
                live.discard(token.serial)
            self.assertEqual(lock.live_count(), len(live))

    def test_lease_survives_ttl_without_refreshing_reservation(self):
        lock = ReservationLock(30)
        token, = lock.acquire()
        status, lease = lock.pin(token)
        self.assertEqual(status, PinStatus.PINNED)
        time.sleep(0.06)
        self.assertEqual(lock.live_count(), 0)
        self.assertEqual(lock.active_count(), 1)
        self.assertTrue(lock.is_locked())
        self.assertEqual(lock.pin(token), (PinStatus.STALE_EPOCH, None))
        self.assertEqual(lock.release(token), Status.STALE_EPOCH)
        self.assertEqual(lock.unpin(lease), Status.RELEASED)
        self.assertFalse(lock.is_locked())

    def test_reset_retains_old_lease_and_isolates_new_lease(self):
        lock = ReservationLock(300000)
        token, = lock.acquire()
        _, old = lock.pin(token)
        lock.reset()
        new_token, = lock.acquire()
        _, new = lock.pin(new_token)
        self.assertEqual(lock.pin(token), (PinStatus.STALE_EPOCH, None))
        self.assertEqual(lock.active_count(), 2)
        self.assertEqual(lock.unpin(old), Status.RELEASED)
        self.assertEqual(lock.unpin(old), Status.INACTIVE)
        self.assertEqual(lock.active_count(), 1)
        self.assertEqual(lock.release(new_token), Status.ACTIVE_LEASE)
        self.assertEqual(lock.unpin(new), Status.RELEASED)
        self.assertEqual(lock.release(new_token), Status.RELEASED)

    def test_multiple_leases_prevent_early_reservation_release(self):
        lock = ReservationLock(300000)
        first, second = lock.acquire(2)
        _, a = lock.pin(first)
        _, b = lock.pin(first)
        _, c = lock.pin(second)
        lock.unpin(a)
        self.assertEqual(lock.release(first), Status.ACTIVE_LEASE)
        lock.unpin(b)
        self.assertEqual(lock.release(first), Status.RELEASED)
        self.assertEqual(lock.release(second), Status.ACTIVE_LEASE)
        lock.unpin(c)
        self.assertEqual(lock.release(second), Status.RELEASED)

    def test_foreign_and_inactive_pin_unpin_do_not_mutate_owner(self):
        lock, other = ReservationLock(300000), ReservationLock(300000)
        token, = lock.acquire()
        _, lease = lock.pin(token)
        self.assertEqual(other.pin(token), (PinStatus.FOREIGN_LOCK, None))
        self.assertEqual(other.unpin(lease), Status.FOREIGN_LOCK)
        self.assertEqual(lock.active_count(), 1)
        lock.unpin(lease)
        lock.release(token)
        self.assertEqual(lock.pin(token), (PinStatus.INACTIVE, None))
        self.assertEqual(lock.unpin(lease), Status.INACTIVE)

    def test_racing_pin_release_is_atomic(self):
        lock = ReservationLock(300000)
        for _ in range(100):
            token, = lock.acquire()
            barrier = threading.Barrier(2)
            def pin():
                barrier.wait()
                return lock.pin(token)
            def release():
                barrier.wait()
                return lock.release(token)
            with ThreadPoolExecutor(2) as pool:
                p, r = pool.submit(pin), pool.submit(release)
                status, lease = p.result()
                released = r.result()
            if status == PinStatus.PINNED:
                self.assertEqual(released, Status.ACTIVE_LEASE)
                self.assertTrue(lock.is_locked())
                lock.unpin(lease)
                lock.release(token)
            else:
                self.assertEqual(status, PinStatus.INACTIVE)
                self.assertEqual(released, Status.RELEASED)
            self.assertFalse(lock.is_locked())

    def test_racing_duplicate_unpin_has_one_winner(self):
        lock = ReservationLock(300000)
        token, = lock.acquire()
        _, lease = lock.pin(token)
        barrier = threading.Barrier(8)
        def unpin(_):
            barrier.wait()
            return lock.unpin(lease)
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(unpin, range(8)))
        self.assertEqual(results.count(Status.RELEASED), 1)
        self.assertEqual(results.count(Status.INACTIVE), 7)
        self.assertEqual(lock.active_count(), 0)
        self.assertEqual(lock.release(token), Status.RELEASED)

    def test_concurrent_leases_with_gil_released(self):
        lock = ReservationLock(300000)
        barrier = threading.Barrier(8)
        def reader(_):
            token, = lock.acquire()
            leases = [lock.pin(token)[1] for _ in range(64)]
            barrier.wait()
            self.assertEqual(lock.release(token), Status.ACTIVE_LEASE)
            for lease in leases:
                self.assertEqual(lock.unpin(lease), Status.RELEASED)
            self.assertEqual(lock.release(token), Status.RELEASED)
            return {(p.lock_id, p.serial) for p in leases}
        with ThreadPoolExecutor(8) as pool:
            sets = list(pool.map(reader, range(8)))
        self.assertEqual(len(set.union(*sets)), 512)
        self.assertFalse(lock.is_locked())


if __name__ == "__main__":
    unittest.main()
