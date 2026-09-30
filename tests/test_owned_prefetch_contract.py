"""Real controller/L1 methods and C++ tokens; mock allocator and L2 I/O."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from owned_prefetch_contract import OwnedPrefetchHarness, JobHandle, token_id
    from reservation_l1_contract import ReservationL1Harness
    from lmcache.lmcache_native import Bitmap
    from lmcache.v1.distributed.api import ObjectKey, PrefetchMode, TrimPolicy, AttnWindowDesc
    from lmcache.v1.distributed.error import L1Error
    from lmcache.v1.distributed.storage_controllers.prefetch_controller import PrefetchPhase
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    OwnedPrefetchHarness = None


@unittest.skipUnless(OwnedPrefetchHarness, "Needs compiled reservation lock and pinned LMCache")
class OwnedPrefetchTests(unittest.TestCase):
    def setUp(self):
        self.allocator = Mock()
        self.allocator.allocate.side_effect = lambda layout, count: (
            L1Error.SUCCESS, [Mock(name=f"buffer-{i}") for i in range(count)])
        self.l1 = ReservationL1Harness(self.allocator, Mock())
        self.adapter = Mock()
        self.controller = OwnedPrefetchHarness(self.l1, Mock(), adapters={0: self.adapter},
            descriptors={0: SimpleNamespace(type_name="CPU-fixture")})
        self.keys = tuple(ObjectKey(bytes([i]), "test", 0) for i in range(5))

    def bitmap(self, size, indices):
        result = Bitmap(size)
        result.batched_set(indices)
        return result

    def seed(self, indices):
        for i in indices:
            self.l1.reserve_write([self.keys[i]], [False], Mock())
            self.l1.finish_write([self.keys[i]])

    def stage_load(self, handle, indices):
        """Supply what the real load planner/adapter would have produced."""
        job = self.controller.jobs[handle.sequence]
        request = job.request
        keys = [job.keys[i] for i in indices]
        result = self.l1.reserve_write(keys, [job.mode != PrefetchMode.WARM] * len(keys), Mock())
        self.assertTrue(all(value[0] == L1Error.SUCCESS for value in result.values()))
        request.write_reserved_keys = keys
        request.write_reserved_objs = {key: result[key][1] for key in keys}
        request.load_plan = {0: self.bitmap(len(job.keys), indices)}
        request.pending_load_tasks = {0: handle.sequence}
        request.l2_adapter2readlocks = {0: self.bitmap(len(job.keys), indices)}
        request.phase = PrefetchPhase.PLAN_AND_LOAD
        self.controller._status_lookup_phase_count -= 1
        self.controller._status_load_phase_count += 1
        self.adapter.query_load_result.return_value = None

    def load_result(self, handle, successful_local_indices):
        request = self.controller.jobs[handle.sequence].request
        self.adapter.query_load_result.return_value = self.bitmap(
            request.load_plan[0].popcount(), successful_local_indices)
        self.assertTrue(self.controller.poll_load(handle, {0}))

    def assert_drained(self):
        self.assertFalse(self.controller.jobs)
        self.assertFalse(self.controller._in_flight_requests)
        self.assertFalse(self.controller._completed_results)
        self.assertFalse(self.controller._completed_lookups)
        self.assertEqual(self.controller._status_in_flight_count, 0)
        self.assertEqual(self.controller._status_lookup_phase_count, 0)
        self.assertEqual(self.controller._status_load_phase_count, 0)
        self.assertTrue(all(not e.read_lock.is_locked() and not e.write_lock.is_locked()
                            for e in self.l1._objects.values()))

    def release_consumer(self, completion):
        results = self.l1.finish_read_owned(completion.reservations)
        self.assertTrue(all(r.status == "released" and r.error is None for r in results))

    def test_l1_query_transfers_exact_captured_tokens_once(self):
        self.seed(range(3))
        handle = self.controller.begin("r", self.keys[:3], readers=2)
        captured = set(self.controller.jobs[handle.sequence].tokens)
        self.assertTrue(self.controller.finish(handle))
        result = self.controller.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1, 2))
        self.assertEqual({token_id(r) for r in result.reservations}, captured)
        self.assertIsNone(self.controller.query_owned(handle))
        self.assertFalse(self.controller.abandon(handle))
        self.release_consumer(result)
        self.assert_drained()

    def test_abandon_pending_waits_for_actual_poll_terminal_result(self):
        self.seed([0])
        handle = self.controller.begin("r", self.keys[:3])
        self.stage_load(handle, [1, 2])
        self.controller.abandon(handle)
        self.assertIsNone(self.controller.query_owned(handle))
        self.assertFalse(self.controller.poll_load(handle, {0}))
        self.assertFalse(self.controller.finish(handle))
        self.assertTrue(self.l1._objects[self.keys[0]].read_lock.is_locked())
        self.assertTrue(self.l1._objects[self.keys[1]].write_lock.is_locked())
        self.assertEqual(self.controller.reclaimed, 0)
        self.load_result(handle, [0, 1])
        self.assertTrue(self.controller.finish(handle))
        self.assertEqual(self.controller.reclaimed, 1)
        self.adapter.submit_unlock.assert_called_once_with(list(self.keys[1:3]))
        self.assert_drained()

    def test_partial_l2_load_trims_tokens_and_keeps_l1_prefix(self):
        self.seed([0])
        handle = self.controller.begin("r", self.keys, readers=2)
        self.stage_load(handle, [1, 2, 3, 4])
        self.load_result(handle, [0, 2, 3])  # Hole at global index 2.
        self.assertTrue(self.controller.finish(handle))
        job = self.controller.jobs[handle.sequence]
        self.assertEqual(len(job.release_results), 4)  # Two readers of each trimmed key.
        self.assertTrue(all(r.status == "released" for r in job.release_results))
        result = self.controller.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1))
        self.assertEqual(len(result.reservations), 4)
        self.assertEqual(set(self.l1._objects), set(self.keys[:2]))
        self.release_consumer(result)
        self.assert_drained()

    def test_sparse_result_preserves_noncontiguous_identity(self):
        handle = self.controller.begin("r", self.keys, policy=TrimPolicy.SPARSE)
        self.stage_load(handle, [0, 1, 2, 3, 4])
        self.load_result(handle, [1, 3])
        self.assertTrue(self.controller.finish(handle))
        result = self.controller.query_owned(handle)
        self.assertEqual(result.retained_indices, (1, 3))
        self.assertEqual({r.key for r in result.reservations}, {self.keys[1], self.keys[3]})
        self.release_consumer(result)
        self.assert_drained()

    def test_initial_sliding_window_trim_uses_captured_tokens(self):
        self.seed(range(5))
        handle = self.controller.begin("r", self.keys, attn_desc=AttnWindowDesc([2]))
        job = self.controller.jobs[handle.sequence]
        self.assertEqual(len(job.release_results), 3)
        self.assertTrue(self.controller.finish(handle))
        result = self.controller.query_owned(handle)
        self.assertEqual(result.retained_indices, (3, 4))
        self.release_consumer(result)
        self.assert_drained()

    def test_warm_completion_does_not_transfer_read_tokens(self):
        self.seed([0])
        handle = self.controller.begin("warm", self.keys[:3], mode=PrefetchMode.WARM)
        self.stage_load(handle, [1, 2])
        self.load_result(handle, [0, 1])
        self.assertTrue(self.controller.finish(handle))
        result = self.controller.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1, 2))
        self.assertFalse(result.reservations)
        self.assertEqual(set(self.l1._objects), set(self.keys[:3]))
        self.assert_drained()

    def test_empty_result_is_terminal_and_consumed_once(self):
        handle = self.controller.begin("empty", self.keys)
        self.assertTrue(self.controller.finish(handle))
        result = self.controller.query_owned(handle)
        self.assertFalse(result.retained_indices)
        self.assertFalse(result.reservations)
        self.assertIsNone(self.controller.query_owned(handle))
        self.assert_drained()

    def test_abandon_ready_prevents_query_and_releases_once(self):
        self.seed([0])
        handle = self.controller.begin("r", self.keys[:1])
        self.assertTrue(self.controller.finish(handle))
        self.assertTrue(self.controller.abandon(handle))
        self.assertFalse(self.controller.abandon(handle))
        self.assertIsNone(self.controller.query_owned(handle))
        self.assertEqual(self.controller.reclaimed, 1)
        self.assert_drained()

    def test_query_abandon_race_has_one_owner(self):
        self.seed([0])
        with ThreadPoolExecutor(2) as pool:
            for i in range(50):
                handle = self.controller.begin("same-id", self.keys[:1])
                self.assertTrue(self.controller.finish(handle))
                barrier = threading.Barrier(2)
                def query():
                    barrier.wait()
                    return self.controller.query_owned(handle)
                def abandon():
                    barrier.wait()
                    return self.controller.abandon(handle)
                q, a = pool.submit(query), pool.submit(abandon)
                result, abandoned = q.result(), a.result()
                self.assertNotEqual(result is not None, abandoned)
                if result is not None:
                    self.release_consumer(result)
                self.assert_drained()

    def test_same_request_id_late_old_completion_does_not_take_new_tokens(self):
        self.seed([0])
        old = self.controller.begin("same", self.keys[:1])
        old_tokens = set(self.controller.jobs[old.sequence].tokens)
        self.controller.abandon(old)
        new = self.controller.begin("same", self.keys[:1])
        new_tokens = set(self.controller.jobs[new.sequence].tokens)
        self.assertFalse(old_tokens & new_tokens)
        self.assertTrue(self.controller.finish(old))
        self.assertEqual(self.l1._objects[self.keys[0]].read_lock.core.live_count(), 1)
        self.assertIsNone(self.controller.query_owned(old))
        self.assertTrue(self.controller.finish(new))
        result = self.controller.query_owned(new)
        self.assertEqual({token_id(r) for r in result.reservations}, new_tokens)
        self.release_consumer(result)
        self.assert_drained()

    def test_foreign_handle_and_raw_query_cannot_consume_result(self):
        self.seed([0])
        handle = self.controller.begin("r", self.keys[:1])
        self.controller.finish(handle)
        foreign = JobHandle("other-server", handle.sequence, handle.external_request_id)
        self.assertIsNone(self.controller.query_owned(foreign))
        self.assertFalse(self.controller.abandon(foreign))
        with self.assertRaises(RuntimeError):
            self.controller.query_prefetch_result(handle.sequence)
        with self.assertRaises(RuntimeError):
            self.controller._l1_manager.finish_read(list(self.keys[:1]))
        self.release_consumer(self.controller.query_owned(handle))
        self.assert_drained()

    def test_partial_release_failure_is_retained_without_retrying_success(self):
        self.seed([0, 1])
        handle = self.controller.begin("r", self.keys[:2])
        self.controller.finish(handle)
        self.l1._objects[self.keys[1]].write_lock.lock()  # Invalid state on one key.
        self.controller.abandon(handle)
        job = self.controller.jobs[handle.sequence]
        self.assertIsNotNone(job.error)
        self.assertEqual([r.status for r in job.release_results], ["released", "write_locked"])
        self.assertEqual(job.completion.retained_indices, (0, 1))
        self.assertEqual(len(job.completion.reservations), 2)
        self.l1.reserve_read_owned([self.keys[0]])  # Fresh owner must not be unlocked by retry.
        self.controller.abandon(handle)
        self.controller.finish(handle)
        self.assertEqual(len(job.release_results), 2)
        self.assertEqual(self.l1._objects[self.keys[0]].read_lock.core.live_count(), 1)
        self.assertEqual(self.controller.reclaimed, 0)

    def test_ttl_stale_result_does_not_release_new_reader_or_claim_full_cleanup(self):
        self.l1._reservation_ttl_ms = 40
        self.seed([0])
        old = self.controller.begin("r", self.keys[:1])
        self.controller.finish(old)
        time.sleep(0.08)
        self.l1.reserve_read_owned([self.keys[0]])
        self.controller.abandon(old)
        job = self.controller.jobs[old.sequence]
        self.assertIsNotNone(job.error)
        self.assertEqual(job.release_results[0].status, "stale_epoch")
        self.assertEqual(self.l1._objects[self.keys[0]].read_lock.core.live_count(), 1)
        self.assertEqual(self.controller.reclaimed, 0)

    def test_acquisition_notification_failure_keeps_tokens_and_blocks_query(self):
        self.seed([0])
        self.l1._event_bus.publish.side_effect = RuntimeError("event sink failure")
        handle = self.controller.begin("r", self.keys[:1])
        job = self.controller.jobs[handle.sequence]
        self.assertIsNotNone(job.error)
        self.assertEqual(len(job.tokens), 1)
        self.assertFalse(self.controller.finish(handle))
        self.assertIsNone(self.controller.query_owned(handle))
        self.controller.abandon(handle)
        self.assertEqual(len(job.tokens), 1)

    def test_failed_write_to_read_never_publishes_retained_bitmap_as_success(self):
        handle = self.controller.begin("r", self.keys[:2])
        self.stage_load(handle, [0, 1])
        self.load_result(handle, [0, 1])
        # Make one reported-success object no longer a valid writer.
        self.l1._objects[self.keys[1]].write_lock.unlock()
        self.assertFalse(self.controller.finish(handle))
        job = self.controller.jobs[handle.sequence]
        self.assertIsNotNone(job.error)
        self.assertEqual(len(job.tokens), 1)
        self.assertFalse(self.controller._completed_results)
        self.assertIsNone(self.controller.query_owned(handle))

    def test_corrupted_completion_cannot_reconstruct_missing_tokens(self):
        handle = self.controller.begin("r", self.keys[:1])
        job = self.controller.jobs[handle.sequence]
        job.request.l1_readlocks = self.bitmap(1, [0])  # No reservation was acquired.
        self.assertFalse(self.controller.finish(handle))
        self.assertIsNotNone(job.error)
        self.assertFalse(self.controller._completed_results)

    def test_reentrant_abandon_during_release_cannot_consume_twice(self):
        self.seed([0])
        handle = self.controller.begin("r", self.keys[:1])
        self.controller.finish(handle)
        self.l1._event_bus.publish.side_effect = lambda event: self.controller.abandon(handle)
        self.controller.abandon(handle)
        self.assertEqual(self.controller.reclaimed, 1)
        self.assert_drained()

    def test_abandon_inside_completion_notification_is_deferred_until_terminal(self):
        self.seed([0])
        handle = self.controller.begin("r", self.keys[:1])
        queries = []
        def cancel(event):
            self.controller.abandon(handle)
            queries.append(self.controller.query_owned(handle))
        self.controller._event_bus.publish.side_effect = cancel
        self.assertTrue(self.controller.finish(handle))
        self.assertTrue(queries)
        self.assertTrue(all(q is None for q in queries))
        self.assertEqual(self.controller.reclaimed, 1)
        self.assert_drained()

    def test_pending_lookup_blocks_completion_and_key_reordering_is_rejected(self):
        self.seed([0, 1])
        handle = self.controller.begin("r", self.keys[:2])
        job = self.controller.jobs[handle.sequence]
        job.request.pending_lookup_tasks = {0: 0}
        self.assertFalse(self.controller.finish(handle))
        self.assertFalse(job.terminal)
        job.request.pending_lookup_tasks.clear()
        job.request.keys.reverse()
        self.assertFalse(self.controller.finish(handle))
        self.assertIsNotNone(job.error)
        self.assertEqual(len(job.tokens), 2)
        self.assertFalse(self.controller._completed_results)


if __name__ == "__main__":
    unittest.main()
