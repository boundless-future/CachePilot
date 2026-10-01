"""Real StorageManager submit/fold/merge with owned controller/L1 fixtures."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from owned_storage_contract import OwnedStorageHarness
    from owned_prefetch_contract import OwnedPrefetchHarness, JobHandle, token_id
    from reservation_l1_contract import ReservationL1Harness
    from lmcache.lmcache_native import Bitmap
    from lmcache.v1.distributed.api import (
        ObjectKey, PrefetchRequestSpec, PrefetchMode, TrimPolicy, AttnWindowDesc,
    )
    from lmcache.v1.distributed.error import L1Error
    from lmcache.v1.distributed.storage_manager import StorageManager
    from lmcache.v1.distributed.storage_controllers.prefetch_controller import PrefetchPhase
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    OwnedStorageHarness = None


@unittest.skipUnless(OwnedStorageHarness, "Needs compiled reservation lock and pinned LMCache")
class OwnedStorageTests(unittest.TestCase):
    def setUp(self):
        self.allocator = Mock()
        self.allocator.allocate.side_effect = lambda layout, count: (
            L1Error.SUCCESS, [Mock(name=f"buffer-{i}") for i in range(count)])
        self.l1 = ReservationL1Harness(self.allocator, Mock())
        self.adapter = Mock()
        self.controller = OwnedPrefetchHarness(self.l1, Mock(), adapters={0: self.adapter},
            descriptors={0: SimpleNamespace(type_name="CPU-fixture")})
        self.events = Mock()
        self.storage = OwnedStorageHarness(self.l1, self.controller, self.events)
        self.keys = tuple(ObjectKey(bytes([i]), "test", 0) for i in range(5))

    def bitmap(self, size, indices):
        bitmap = Bitmap(size)
        bitmap.batched_set(indices)
        return bitmap

    def spec(self, keys=None, **kwargs):
        return PrefetchRequestSpec(list(self.keys if keys is None else keys), {0: Mock()}, **kwargs)

    def seed(self, indices):
        for i in indices:
            self.l1.reserve_write([self.keys[i]], [False], Mock())
            self.l1.finish_write([self.keys[i]])

    def stage_load(self, outer, indices):
        job = self.storage.jobs[outer.sequence]
        child = self.controller.jobs[job.downstream.sequence]
        request = child.request
        keys = [child.keys[i] for i in indices]
        reserved = self.l1.reserve_write(keys, [child.mode != PrefetchMode.WARM] * len(keys), Mock())
        self.assertTrue(all(r[0] == L1Error.SUCCESS for r in reserved.values()))
        request.write_reserved_keys = keys
        request.write_reserved_objs = {key: reserved[key][1] for key in keys}
        request.load_plan = {0: self.bitmap(len(child.keys), indices)}
        request.pending_load_tasks = {0: child.handle.sequence}
        request.l2_adapter2readlocks = {0: self.bitmap(len(child.keys), indices)}
        request.phase = PrefetchPhase.PLAN_AND_LOAD
        self.controller._status_lookup_phase_count -= 1
        self.controller._status_load_phase_count += 1
        self.adapter.query_load_result.return_value = None

    def complete_load(self, outer, successful_indices):
        job = self.storage.jobs[outer.sequence]
        child = self.controller.jobs[job.downstream.sequence]
        self.adapter.query_load_result.return_value = self.bitmap(
            child.request.load_plan[0].popcount(), successful_indices)
        self.assertTrue(self.controller.poll_load(child.handle, {0}))
        self.assertTrue(self.controller.finish(child.handle))

    def release(self, result):
        self.assertIsNotNone(result)
        released = self.l1.finish_read_owned(result.reservations)
        self.assertTrue(all(r.status == "released" and r.error is None for r in released))

    def assert_drained(self):
        self.assertFalse(self.storage.jobs)
        self.assertFalse(self.controller.jobs)
        self.assertFalse(self.controller._in_flight_requests)
        self.assertFalse(self.controller._completed_results)
        self.assertFalse(self.controller._completed_lookups)
        self.assertTrue(all(not e.read_lock.is_locked() and not e.write_lock.is_locked()
                            for e in self.l1._objects.values()))

    def test_pure_l1_minus_one_is_consumed_once_with_original_tokens(self):
        self.seed(range(3))
        handle = self.storage.submit_owned(self.spec(self.keys[:3], num_kv_readers=2), "r")
        job = self.storage.jobs[handle.sequence]
        self.assertEqual(job.native_handle.prefetch_request_id, -1)
        captured = set(job.tokens)
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1, 2))
        self.assertEqual({token_id(r) for r in result.reservations}, captured)
        self.assertEqual(len(result.reservations), 6)
        self.assertIsNone(self.storage.query_owned(handle))
        self.assertFalse(self.storage.abandon(handle))
        self.release(result)
        self.assert_drained()

    def test_pure_l1_abandon_prevents_query_and_releases_once(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:1]), "r")
        self.assertTrue(self.storage.abandon(handle))
        self.assertFalse(self.storage.abandon(handle))
        self.assertIsNone(self.storage.query_owned(handle))
        self.assertEqual(self.storage.reclaimed, 1)
        self.assert_drained()

    def test_mixed_prefix_merges_initial_and_original_l2_tokens(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:3], num_kv_readers=2), "r")
        job = self.storage.jobs[handle.sequence]
        initial = set(job.tokens)
        self.assertEqual(job.native_handle.l2_orig_indices, (1, 2))
        self.stage_load(handle, [0, 1])
        self.complete_load(handle, [0, 1])
        child_tokens = set(self.controller.jobs[job.downstream.sequence].tokens)
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1, 2))
        self.assertEqual({token_id(r) for r in result.reservations}, initial | child_tokens)
        self.release(result)
        self.assert_drained()

    def test_sparse_noncontiguous_mapping_survives_partial_l2_load(self):
        self.seed([0, 2, 4])
        handle = self.storage.submit_owned(self.spec(policy=TrimPolicy.SPARSE), "sparse")
        self.assertEqual(self.storage.jobs[handle.sequence].native_handle.l2_orig_indices, (1, 3))
        self.stage_load(handle, [0, 1])
        self.complete_load(handle, [1])
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 2, 3, 4))
        self.assertEqual({r.key for r in result.reservations}, {self.keys[i] for i in (0, 2, 3, 4)})
        self.assertNotIn(self.keys[1], self.l1._objects)
        self.release(result)
        self.assert_drained()

    def test_initial_prefix_trim_and_suffix_reacquire_use_different_tokens(self):
        self.seed([0, 2])
        handle = self.storage.submit_owned(self.spec(self.keys[:3]), "gap")
        job = self.storage.jobs[handle.sequence]
        self.assertEqual([r.reservation.key for r in job.release_results], [self.keys[2]])
        old = token_id(job.release_results[0].reservation)
        self.stage_load(handle, [0])  # Suffix key 2 is already resident, load key 1.
        self.complete_load(handle, [0])
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1, 2))
        self.assertNotIn(old, {token_id(r) for r in result.reservations})
        self.release(result)
        self.assert_drained()

    def test_pure_l1_sliding_window_releases_initial_out_of_window_tokens(self):
        self.seed(range(5))
        handle = self.storage.submit_owned(self.spec(attn_desc=AttnWindowDesc([2])), "window")
        job = self.storage.jobs[handle.sequence]
        self.assertEqual(len(job.release_results), 3)
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (3, 4))
        self.release(result)
        self.assert_drained()

    def test_warm_skip_l2_and_empty_paths_transfer_no_anonymous_references(self):
        self.seed([0])
        specs = [self.spec(self.keys[:1], mode=PrefetchMode.WARM), self.spec([]),
                 self.spec(self.keys[:3], policy=TrimPolicy.SPARSE)]
        for spec in specs:
            with self.subTest(mode=spec.mode, size=len(spec.keys)):
                handle = self.storage.submit_owned(spec, "skip", skip_l2=True)
                result = self.storage.query_owned(handle)
                self.assertEqual(result.retained_indices, (0,) if spec.policy == TrimPolicy.SPARSE else ())
                self.release(result)
                self.assert_drained()

    def test_warm_l2_keeps_loaded_objects_without_read_tokens(self):
        handle = self.storage.submit_owned(self.spec(self.keys[:2], mode=PrefetchMode.WARM), "warm")
        self.stage_load(handle, [0, 1])
        self.complete_load(handle, [0, 1])
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1))
        self.assertFalse(result.reservations)
        self.assertEqual(set(self.l1._objects), set(self.keys[:2]))
        self.assert_drained()

    def test_abandon_pending_keeps_initial_tokens_until_controller_terminal(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:3]), "pending")
        self.stage_load(handle, [0, 1])
        self.storage.abandon(handle)
        self.assertFalse(self.storage.advance(handle))
        self.assertTrue(self.l1._objects[self.keys[0]].read_lock.is_locked())
        self.assertEqual(self.storage.reclaimed, 0)
        self.assertIsNone(self.storage.query_owned(handle))
        self.complete_load(handle, [0, 1])
        self.assertTrue(self.storage.advance(handle))
        self.assertEqual(self.storage.reclaimed, 1)
        self.assertEqual(set(self.l1._objects), {self.keys[0]})
        self.assert_drained()

    def test_same_external_id_and_minus_one_do_not_share_ownership(self):
        self.seed([0])
        old = self.storage.submit_owned(self.spec(self.keys[:1]), "same")
        new = self.storage.submit_owned(self.spec(self.keys[:1]), "same")
        new_tokens = set(self.storage.jobs[new.sequence].tokens)
        self.storage.abandon(old)
        self.assertEqual(self.l1._objects[self.keys[0]].read_lock.core.live_count(), 1)
        self.assertIsNone(self.storage.query_owned(old))
        result = self.storage.query_owned(new)
        self.assertEqual({token_id(r) for r in result.reservations}, new_tokens)
        self.release(result)
        self.assert_drained()

    def test_query_abandon_race_has_exactly_one_owner(self):
        self.seed([0])
        with ThreadPoolExecutor(2) as pool:
            for i in range(50):
                handle = self.storage.submit_owned(self.spec(self.keys[:1]), "same")
                barrier = threading.Barrier(2)
                def query():
                    barrier.wait()
                    return self.storage.query_owned(handle)
                def abandon():
                    barrier.wait()
                    return self.storage.abandon(handle)
                q, a = pool.submit(query), pool.submit(abandon)
                result, abandoned = q.result(), a.result()
                self.assertNotEqual(result is not None, abandoned)
                if result is not None:
                    self.release(result)
                self.assert_drained()

    def test_foreign_handle_and_legacy_queries_are_rejected(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:1]))
        foreign = JobHandle("foreign", handle.sequence, handle.external_request_id)
        self.assertIsNone(self.storage.query_owned(foreign))
        self.assertFalse(self.storage.abandon(foreign))
        with self.assertRaises(RuntimeError):
            self.storage.query_prefetch_status(self.storage.jobs[handle.sequence].native_handle)
        with self.assertRaises(RuntimeError):
            self.storage.submit_prefetch_task(self.spec())
        self.release(self.storage.query_owned(handle))
        self.assert_drained()

    def test_bad_mapping_is_rejected_before_destructive_child_query(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:3]))
        job = self.storage.jobs[handle.sequence]
        self.stage_load(handle, [0, 1])
        self.complete_load(handle, [0, 1])
        job.native_handle = replace(job.native_handle, l2_orig_indices=(2, 1))
        self.assertIsNone(self.storage.query_owned(handle))
        self.assertIsNotNone(job.error)
        self.assertIn(job.downstream.sequence, self.controller.jobs)
        self.assertIsNone(job.downstream_completion)

    def test_out_of_range_or_duplicate_mapping_cannot_be_silently_dropped(self):
        for mapping in ((1, 99), (1, 1)):
            with self.subTest(mapping=mapping):
                handle = self.storage.submit_owned(self.spec(self.keys[:2]))
                job = self.storage.jobs[handle.sequence]
                job.native_handle = replace(job.native_handle, l2_orig_indices=mapping)
                self.assertFalse(self.storage.advance(handle))
                self.assertIsNotNone(job.error)

    def test_merge_failure_after_child_transfer_keeps_all_original_tokens(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:2]))
        job = self.storage.jobs[handle.sequence]
        self.stage_load(handle, [0])
        self.complete_load(handle, [0])
        initial = set(job.tokens)
        child = set(self.controller.jobs[job.downstream.sequence].tokens)
        with patch.object(StorageManager, "_combine_found", return_value=self.bitmap(2, [0])):
            self.assertIsNone(self.storage.query_owned(handle))
        self.assertEqual(set(job.tokens), initial | child)
        self.assertIsNotNone(job.downstream_completion)
        self.assertIsNotNone(job.error)
        self.assertFalse(self.controller.jobs)
        self.storage.abandon(handle)
        self.assertEqual(set(job.tokens), initial | child)

    def test_acquisition_and_storage_notification_failures_preserve_tokens(self):
        self.seed([0])
        for sink in (self.l1._event_bus, self.events):
            with self.subTest(sink=sink):
                sink.publish.side_effect = RuntimeError("notification failed")
                handle = self.storage.submit_owned(self.spec(self.keys[:1]))
                sink.publish.side_effect = None
                job = self.storage.jobs[handle.sequence]
                self.assertIsNotNone(job.error)
                self.assertEqual(len(job.tokens), 1)
                self.assertIsNone(self.storage.query_owned(handle))
                self.storage.abandon(handle)
                self.assertEqual(len(job.tokens), 1)

    def test_initial_trim_release_failure_does_not_retry_known_success(self):
        self.seed([0, 2, 3])
        real = self.l1.finish_read_owned
        def release_then_fail(reservations):
            results = real(reservations)
            return (replace(results[0], error="notification failed"),) + results[1:]
        self.l1.finish_read_owned = release_then_fail
        handle = self.storage.submit_owned(self.spec())
        job = self.storage.jobs[handle.sequence]
        self.assertIsNotNone(job.error)
        self.assertEqual(len(job.release_results), 2)
        self.assertEqual({r.key for r in job.tokens.values()}, {self.keys[0]})
        fresh = self.l1.reserve_read_owned([self.keys[2]]).reservations
        self.storage.abandon(handle)
        self.assertEqual(len(job.release_results), 2)
        self.assertEqual(self.l1._objects[self.keys[2]].read_lock.core.live_count(), len(fresh))

    def test_failure_after_downstream_submission_retains_both_owners(self):
        self.seed([0])
        real = StorageManager.submit_prefetch_task
        def submit_then_fail(instance, *args, **kwargs):
            real(instance, *args, **kwargs)
            raise RuntimeError("failure after downstream queued")
        with patch.object(StorageManager, "submit_prefetch_task", submit_then_fail):
            handle = self.storage.submit_owned(self.spec(self.keys[:2]))
        job = self.storage.jobs[handle.sequence]
        self.assertIsNotNone(job.error)
        self.assertEqual(len(job.tokens), 1)
        self.assertIn(job.downstream.sequence, self.controller.jobs)
        self.storage.abandon(handle)
        self.assertEqual(len(job.tokens), 1)

    def test_child_failure_blocks_merge_and_preserves_initial_reservations(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:2]))
        job = self.storage.jobs[handle.sequence]
        child = self.controller.jobs[job.downstream.sequence]
        child.error = "injected controller I/O error"
        self.assertIsNone(self.storage.query_owned(handle))
        self.assertIsNotNone(job.error)
        self.assertEqual(len(job.tokens), 1)
        self.assertIn(child.handle.sequence, self.controller.jobs)

    def test_partial_reap_failure_retains_completion_without_success_retry(self):
        self.seed([0, 1])
        handle = self.storage.submit_owned(self.spec(self.keys[:2]))
        self.l1._objects[self.keys[1]].write_lock.lock()
        self.storage.abandon(handle)
        job = self.storage.jobs[handle.sequence]
        self.assertIsNotNone(job.error)
        self.assertEqual([r.status for r in job.release_results], ["released", "write_locked"])
        self.assertEqual(job.completion.retained_indices, (0, 1))
        self.l1.reserve_read_owned([self.keys[0]])
        self.storage.abandon(handle)
        self.assertEqual(len(job.release_results), 2)
        self.assertEqual(self.l1._objects[self.keys[0]].read_lock.core.live_count(), 1)
        self.assertEqual(self.storage.reclaimed, 0)

    def test_ttl_stale_initial_token_cannot_release_new_reader(self):
        self.l1._reservation_ttl_ms = 40
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:1]))
        time.sleep(0.08)
        self.l1.reserve_read_owned([self.keys[0]])
        self.storage.abandon(handle)
        job = self.storage.jobs[handle.sequence]
        self.assertEqual(job.release_results[0].status, "stale_epoch")
        self.assertIsNotNone(job.error)
        self.assertEqual(self.l1._objects[self.keys[0]].read_lock.core.live_count(), 1)
        self.assertEqual(self.storage.reclaimed, 0)

    def test_reentrant_abandon_and_query_during_submit_and_release(self):
        self.seed([0])
        def cancel(event):
            handle = next(iter(self.storage.jobs.values())).handle
            self.assertTrue(self.storage.abandon(handle))
            self.assertIsNone(self.storage.query_owned(handle))
        self.events.publish.side_effect = cancel
        self.l1._event_bus.publish.side_effect = cancel
        handle = self.storage.submit_owned(self.spec(self.keys[:1]))
        self.assertIsNone(self.storage.query_owned(handle))
        self.assertEqual(self.storage.reclaimed, 1)
        self.assert_drained()

    def test_unsupported_inputs_are_rejected_before_any_reservation(self):
        self.seed([0])
        specs = [self.spec(self.keys[:1], policy=TrimPolicy.SEGMENTED_PREFIX),
                 self.spec([self.keys[0], self.keys[0]]),
                 self.spec(self.keys[:1], num_kv_readers=129),
                 self.spec(self.keys[:1], attn_desc=AttnWindowDesc([-1], world_size=2))]
        for spec in specs:
            with self.subTest(policy=spec.policy, readers=spec.num_kv_readers):
                with self.assertRaises(ValueError):
                    self.storage.submit_owned(spec)
                self.assert_drained()

    def test_mutating_callers_key_list_does_not_change_submission(self):
        self.seed([0])
        spec = self.spec(self.keys[:2])
        handle = self.storage.submit_owned(spec)
        spec.keys.reverse()
        self.stage_load(handle, [0])
        self.complete_load(handle, [0])
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1))
        self.assertEqual({r.key for r in result.reservations}, set(self.keys[:2]))
        self.release(result)
        self.assert_drained()

    def test_ranked_group_prefix_and_sliding_retention_use_original_positions(self):
        self.keys = tuple(ObjectKey(bytes([chunk]), "test", rank, group)
            for chunk in range(3) for group in range(2) for rank in range(2))
        self.seed(range(11))  # One rank of the final group/chunk is missing.
        spec = PrefetchRequestSpec(list(self.keys), {0: Mock(), 1: Mock()},
            attn_desc=AttnWindowDesc([-1, 1], world_size=2), num_kv_readers=2)
        handle = self.storage.submit_owned(spec, "ranked", skip_l2=True)
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0, 1, 4, 5, 6, 7))
        self.assertEqual(len(result.reservations), 12)
        self.release(result)
        self.assert_drained()

    def test_no_adapters_uses_one_time_l1_result_without_controller(self):
        self.controller._l2_adapters.clear()
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:2]))
        result = self.storage.query_owned(handle)
        self.assertEqual(result.retained_indices, (0,))
        self.assertIsNone(self.storage.query_owned(handle))
        self.release(result)
        self.assert_drained()

    def test_malformed_local_completion_is_retained_after_child_transfer(self):
        handle = self.storage.submit_owned(self.spec(self.keys[:2]))
        job = self.storage.jobs[handle.sequence]
        self.stage_load(handle, [0, 1])
        self.complete_load(handle, [0, 1])
        child = self.controller.jobs[job.downstream.sequence]
        tokens = set(child.tokens)
        self.controller._completed_results[child.handle.sequence] = replace(
            child.completion, retained_indices=(0, 2))
        self.assertIsNone(self.storage.query_owned(handle))
        self.assertIsNotNone(job.error)
        self.assertEqual(set(job.tokens), tokens)
        self.assertEqual(job.downstream_completion.retained_indices, (0, 2))
        self.assertFalse(self.controller.jobs)

    def test_external_child_consumption_is_reported_as_unresolved(self):
        handle = self.storage.submit_owned(self.spec(self.keys[:1]))
        job = self.storage.jobs[handle.sequence]
        self.assertTrue(self.controller.finish(job.downstream))
        self.assertIsNotNone(self.controller.query_owned(job.downstream))
        self.assertIsNone(self.storage.query_owned(handle))
        self.assertIn("disappeared", job.error)
        self.assertEqual(self.storage.reclaimed, 0)

    def test_reentrant_cancel_during_merge_cannot_transfer_to_caller(self):
        self.seed([0])
        handle = self.storage.submit_owned(self.spec(self.keys[:2]))
        self.stage_load(handle, [0])
        self.complete_load(handle, [0])
        real = StorageManager._combine_found
        def merge(instance, *args):
            self.assertTrue(instance.abandon(handle))
            self.assertIsNone(instance.query_owned(handle))
            return real(instance, *args)
        with patch.object(StorageManager, "_combine_found", merge):
            self.assertIsNone(self.storage.query_owned(handle))
        self.assertEqual(self.storage.reclaimed, 1)
        self.assert_drained()


if __name__ == "__main__":
    unittest.main()
