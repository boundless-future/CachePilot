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
    from cachepilot_reservation_native import ReservationLock, ReleaseStatus as Status
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


if __name__ == "__main__":
    unittest.main()
