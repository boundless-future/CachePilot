"""Real LookupModule folding with original tokens and simulated worker acks."""
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
    from owned_lookup_contract import OwnedLookupHarness, Worker, ReaderTicket
    from owned_storage_contract import OwnedStorageHarness
    from owned_prefetch_contract import OwnedPrefetchHarness, JobHandle, token_id
    from reservation_l1_contract import ReservationL1Harness
    from lmcache.lmcache_native import Bitmap
    from lmcache.v1.distributed.api import AttnWindowDesc, ObjectKey
    from lmcache.v1.distributed.error import L1Error
    from lmcache.v1.multiprocess.custom_types import IPCCacheServerKey
    from lmcache.v1.distributed.storage_controllers.prefetch_controller import PrefetchPhase
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    OwnedLookupHarness = None


@unittest.skipUnless(OwnedLookupHarness, "Needs compiled reservation lock and pinned LMCache")
class OwnedLookupTests(unittest.TestCase):
    def setUp(self):
        self.allocator = Mock()
        self.allocator.allocate.side_effect = lambda layout, count: (
            L1Error.SUCCESS, [Mock(name=f"buffer-{i}") for i in range(count)])
        self.l1 = ReservationL1Harness(self.allocator, Mock())
        self.adapter = Mock()
        self.controller = OwnedPrefetchHarness(self.l1, Mock(), adapters={0: self.adapter},
            descriptors={0: SimpleNamespace(type_name="CPU-fixture")})
        self.storage = OwnedStorageHarness(self.l1, self.controller, Mock())
        self.ctx = Mock()
        self.ctx.chunk_size = 256
        self.ctx.event_bus.has_subscribers.return_value = False
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1])
        self.ctx.layout_desc_registry.find_group_layout_descs.return_value = {0: Mock()}
        self.hashes = [b'a', b'b', b'c']
        self.ctx.token_hasher.compute_chunk_hashes.return_value = self.hashes
        self.ctx.session_manager.remove.return_value = None
        self.module = OwnedLookupHarness(self.ctx, self.storage)
        self.worker = Worker("worker-incarnation-1", 0)

    def key(self, request_id="r", readers=1, world_size=1):
        return IPCCacheServerKey.from_token_ids("test", world_size, None,
            list(range(256 * len(self.hashes))), end=256 * len(self.hashes),
            request_id=request_id, num_kv_readers=readers)

    def seed(self, key=None, indices=None):
        key = key or self.key()
        keys = self.module._chunk_major_object_keys(key, self.hashes)
        for i in range(len(keys)) if indices is None else indices:
            self.l1.reserve_write([keys[i]], [False], Mock())
            self.l1.finish_write([keys[i]])
        return keys

    def begin(self, key=None, workers=None):
        return self.module.begin(key or self.key(), workers or (self.worker,))

    def bitmap(self, size, indices):
        bitmap = Bitmap(size)
        bitmap.batched_set(indices)
        return bitmap

    def stage_load(self, handle, local_indices):
        outer = self.module.jobs[handle.sequence]
        storage = self.storage.jobs[outer.storage_handle.sequence]
        child = self.controller.jobs[storage.downstream.sequence]
        request = child.request
        keys = [child.keys[i] for i in local_indices]
        reserved = self.l1.reserve_write(keys, [True] * len(keys), Mock())
        self.assertTrue(all(r[0] == L1Error.SUCCESS for r in reserved.values()))
        request.write_reserved_keys = keys
        request.write_reserved_objs = {key: reserved[key][1] for key in keys}
        request.load_plan = {0: self.bitmap(len(child.keys), local_indices)}
        request.pending_load_tasks = {0: child.handle.sequence}
        request.l2_adapter2readlocks = {0: self.bitmap(len(child.keys), local_indices)}
        request.phase = PrefetchPhase.PLAN_AND_LOAD
        self.controller._status_lookup_phase_count -= 1
        self.controller._status_load_phase_count += 1
        self.adapter.query_load_result.return_value = None
        return child

    def complete_load(self, child, indices):
        self.adapter.query_load_result.return_value = self.bitmap(child.request.load_plan[0].popcount(), indices)
        self.assertTrue(self.controller.poll_load(child.handle, {0}))
        self.assertTrue(self.controller.finish(child.handle))

    def finish_all(self, completion, succeeded=True):
        for ticket in completion.tickets:
            self.assertIsNotNone(self.module.claim_retrieve(ticket))
            self.assertTrue(self.module.finish_retrieve(ticket, succeeded=succeeded))

    def assert_drained(self):
        self.assertFalse(self.module.jobs)
        self.assertFalse(self.module._latest)
        self.assertFalse(self.module._prefetch_jobs)
        self.assertFalse(self.storage.jobs)
        self.assertFalse(self.controller.jobs)
        self.assertFalse(self.controller._completed_results)
        self.assertTrue(all(not e.read_lock.is_locked() and not e.write_lock.is_locked()
                            for e in self.l1._objects.values()))

    def test_real_lookup_query_records_hits_and_preserves_original_tokens(self):
        self.seed()
        handle = self.begin()
        job = self.module.jobs[handle.sequence]
        captured = set(self.storage.jobs[job.storage_handle.sequence].tokens)
        completion = self.module.query_owned(handle)
        self.assertEqual(completion.hit_chunks, 3)
        self.ctx.session_manager.get_or_create.return_value.record_prefetch_result.assert_called_once_with(3, (0,))
        claim = self.module.claim_retrieve(completion.tickets[0])
        self.assertEqual({token_id(r) for r in claim.reservations}, captured)
        self.assertIsNone(self.module.query_owned(handle))
        self.assertIsNone(self.module.claim_retrieve(claim.ticket))
        self.assertTrue(self.module.finish_retrieve(claim.ticket, succeeded=True))
        self.assertFalse(self.module.finish_retrieve(claim.ticket, succeeded=True))
        self.assert_drained()

    def test_shared_rank_readers_get_disjoint_original_tokens(self):
        key = self.key(readers=2)
        self.seed(key)
        other = Worker("worker-incarnation-2", 0)
        handle = self.begin(key, (self.worker, other))
        completion = self.module.query_owned(handle)
        first = self.module.claim_retrieve(completion.tickets[0])
        second = self.module.claim_retrieve(completion.tickets[1])
        self.assertEqual({r.key for r in first.reservations}, {r.key for r in second.reservations})
        self.assertFalse({token_id(r) for r in first.reservations} & {token_id(r) for r in second.reservations})
        self.assertTrue(self.module.finish_retrieve(first.ticket, succeeded=False))
        self.assertTrue(all(e.read_lock.core.live_count() == 1 for e in self.l1._objects.values()))
        self.assertTrue(self.module.finish_retrieve(second.ticket, succeeded=True))
        self.assert_drained()

    def test_two_ranks_only_claim_their_own_shard(self):
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1], world_size=2)
        key = self.key(world_size=2)
        self.seed(key)
        workers = (self.worker, Worker("rank-1-incarnation", 1))
        handle = self.begin(key, workers)
        completion = self.module.query_owned(handle)
        self.assertEqual(completion.hit_chunks, 3)
        for ticket in completion.tickets:
            claim = self.module.claim_retrieve(ticket)
            expected_rank = ObjectKey.ComputeKVRank(2, ticket.worker.rank, 2, ticket.worker.rank)
            self.assertEqual({r.key.kv_rank for r in claim.reservations}, {expected_rank})
            self.assertEqual(len(claim.reservations), 3)
            self.assertTrue(self.module.finish_retrieve(ticket, succeeded=True))
        self.assert_drained()

    def test_end_after_query_releases_only_unclaimed_slots(self):
        key = self.key(readers=2)
        self.seed(key)
        handle = self.begin(key, (self.worker, Worker("second", 0)))
        completion = self.module.query_owned(handle)
        claim = self.module.claim_retrieve(completion.tickets[0])
        self.assertTrue(self.module.end_owned(handle))
        self.assertIsNone(self.module.claim_retrieve(completion.tickets[1]))
        self.assertTrue(all(e.read_lock.core.live_count() == 1 for e in self.l1._objects.values()))
        self.assertIn(handle.sequence, self.module.jobs)
        self.assertTrue(self.module.finish_retrieve(claim.ticket, succeeded=False))
        self.assertEqual(self.module.closed, 1)
        self.assert_drained()

    def test_end_before_query_delegates_reclaim_to_storage(self):
        self.seed()
        handle = self.begin()
        self.assertTrue(self.module.end_owned(handle))
        self.assertIsNone(self.module.query_owned(handle))
        self.assertEqual(self.storage.reclaimed, 1)
        self.ctx.session_manager.remove.assert_called_once_with("r")
        self.assert_drained()

    def test_pending_end_and_late_controller_completion_do_not_recreate_session(self):
        keys = self.seed(indices=[0])
        handle = self.begin()
        child = self.stage_load(handle, [0, 1])
        self.module.end_owned(handle)
        self.ctx.session_manager.get_or_create.reset_mock()
        self.assertFalse(self.module.advance(handle))
        self.assertTrue(self.l1._objects[keys[0]].read_lock.is_locked())
        self.assertIsNone(self.module.query_owned(handle))
        self.complete_load(child, [0, 1])
        self.assertTrue(self.module.advance(handle))
        self.ctx.session_manager.get_or_create.assert_not_called()
        self.assertEqual(set(self.l1._objects), {keys[0]})
        self.assert_drained()

    def test_global_sliding_fold_releases_initial_tokens_outside_final_window(self):
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([1])
        keys = self.seed(indices=[0])
        handle = self.begin()
        child = self.stage_load(handle, [0, 1])
        self.complete_load(child, [0, 1])
        completion = self.module.query_owned(handle)
        job = self.module.jobs[handle.sequence]
        self.assertEqual(completion.hit_chunks, 3)
        # Initial L1 and L2 legs retained separate windows: the global fold
        # requires only chunk 2. Its unused initial L1 token is released.
        self.assertEqual([r.reservation.key for r in job.release_results], [keys[0]])
        claim = self.module.claim_retrieve(completion.tickets[0])
        self.assertEqual([r.key for r in claim.reservations], [keys[2]])
        self.assertTrue(self.module.finish_retrieve(claim.ticket, succeeded=True))
        self.assert_drained()

    def test_empty_and_native_early_exit_paths_publish_no_reader_tickets(self):
        for branch in ("missing-layout", "empty-hashes", "missing-group-layout"):
            with self.subTest(branch=branch):
                self.ctx.layout_desc_registry.find.return_value = None if branch == "missing-layout" else Mock()
                self.ctx.token_hasher.compute_chunk_hashes.return_value = [] if branch == "empty-hashes" else self.hashes
                self.ctx.layout_desc_registry.find_group_layout_descs.return_value = {} if branch == "missing-group-layout" else {0: Mock()}
                handle = self.begin()
                completion = self.module.query_owned(handle)
                self.assertEqual(completion.hit_chunks, 0)
                self.assertFalse(completion.tickets)
                self.assert_drained()

    def test_zero_hits_from_real_storage_have_no_slots(self):
        self.controller._l2_adapters.clear()
        handle = self.begin()
        result = self.module.query_owned(handle)
        self.assertEqual(result.hit_chunks, 0)
        self.assertFalse(result.tickets)
        self.assert_drained()

    def test_query_end_race_has_no_lost_or_duplicate_references(self):
        self.seed()
        with ThreadPoolExecutor(2) as pool:
            for i in range(30):
                handle = self.begin()
                barrier = threading.Barrier(2)
                def query():
                    barrier.wait()
                    return self.module.query_owned(handle)
                def end():
                    barrier.wait()
                    return self.module.end_owned(handle)
                q, e = pool.submit(query), pool.submit(end)
                completion, ended = q.result(), e.result()
                self.assertTrue(ended)
                if completion:
                    self.assertTrue(all(self.module.claim_retrieve(t) is None for t in completion.tickets))
                self.assert_drained()

    def test_claim_end_race_either_reclaims_or_waits_for_terminal_ack(self):
        self.seed()
        with ThreadPoolExecutor(2) as pool:
            for i in range(30):
                handle = self.begin()
                completion = self.module.query_owned(handle)
                ticket = completion.tickets[0]
                barrier = threading.Barrier(2)
                def claim():
                    barrier.wait()
                    return self.module.claim_retrieve(ticket)
                def end():
                    barrier.wait()
                    return self.module.end_owned(handle)
                c, e = pool.submit(claim), pool.submit(end)
                claimed, ended = c.result(), e.result()
                self.assertTrue(ended)
                if claimed:
                    self.assertTrue(all(entry.read_lock.is_locked() for entry in self.l1._objects.values()))
                    self.assertTrue(self.module.finish_retrieve(ticket, succeeded=True))
                self.assert_drained()

    def test_same_request_id_old_ack_and_end_cannot_touch_new_generation(self):
        self.seed()
        old = self.begin()
        result = self.module.query_owned(old)
        claim = self.module.claim_retrieve(result.tickets[0])
        self.module.end_owned(old)
        new = self.begin()
        new_result = self.module.query_owned(new)
        new_tokens = set(self.module.jobs[new.sequence].tokens)
        self.ctx.session_manager.remove.reset_mock()
        self.module.end_owned(old)
        self.ctx.session_manager.remove.assert_not_called()
        self.assertTrue(self.module.finish_retrieve(claim.ticket, succeeded=True))
        self.assertEqual(set(self.module.jobs[new.sequence].tokens), new_tokens)
        self.assertFalse(self.module.finish_retrieve(claim.ticket, succeeded=True))
        self.finish_all(new_result)
        self.assert_drained()

    def test_new_worker_incarnation_and_foreign_lookup_cannot_claim_old_slot(self):
        self.seed()
        handle = self.begin()
        result = self.module.query_owned(handle)
        ticket = result.tickets[0]
        wrong_worker = replace(ticket, worker=Worker("restarted-worker", 0))
        wrong_server = replace(ticket, lookup=JobHandle("other-server", handle.sequence, "r"))
        for bad in (wrong_worker, wrong_server):
            self.assertIsNone(self.module.claim_retrieve(bad))
            self.assertFalse(self.module.finish_retrieve(bad, succeeded=False))
        self.finish_all(result)
        self.assert_drained()

    def test_notification_failure_after_storage_transfer_retains_tokens(self):
        self.seed()
        handle = self.begin()
        job = self.module.jobs[handle.sequence]
        self.ctx.event_bus.publish.side_effect = RuntimeError("query notification failed")
        self.assertIsNone(self.module.query_owned(handle))
        self.assertIsNotNone(job.error)
        self.assertEqual(len(job.tokens), 3)
        self.assertIsNotNone(job.storage_completion)
        self.assertFalse(self.storage.jobs)
        self.ctx.event_bus.publish.side_effect = None
        self.module.abandon(handle)
        self.assertEqual(len(job.tokens), 3)
        self.assertEqual(self.module.closed, 0)

    def test_partial_terminal_release_keeps_evidence_and_never_retries_success(self):
        keys = self.seed()
        handle = self.begin()
        result = self.module.query_owned(handle)
        ticket = result.tickets[0]
        self.module.claim_retrieve(ticket)
        self.l1._objects[keys[1]].write_lock.lock()
        self.assertFalse(self.module.finish_retrieve(ticket, succeeded=False))
        job = self.module.jobs[handle.sequence]
        self.assertEqual([r.status for r in job.release_results], ["released", "write_locked", "released"])
        self.assertEqual(job.slots[ticket].state, "terminal")
        self.assertFalse(job.slots[ticket].outcome)
        fresh = self.l1.reserve_read_owned([keys[0]])
        self.assertFalse(self.module.finish_retrieve(ticket, succeeded=False))
        self.module.abandon(handle)
        self.assertEqual(len(job.release_results), 3)
        self.assertEqual(self.l1._objects[keys[0]].read_lock.core.live_count(), len(fresh.reservations))

    def test_ttl_then_new_generation_old_terminal_ack_does_not_unlock_new_readers(self):
        self.l1._reservation_ttl_ms = 40
        self.seed()
        old = self.begin()
        result = self.module.query_owned(old)
        ticket = result.tickets[0]
        self.module.claim_retrieve(ticket)
        self.module.abandon(old)
        time.sleep(0.08)
        new = self.begin()
        new_result = self.module.query_owned(new)
        self.assertFalse(self.module.finish_retrieve(ticket, succeeded=True))
        job = self.module.jobs[old.sequence]
        self.assertTrue(all(r.status == "stale_epoch" for r in job.release_results))
        self.assertTrue(all(e.read_lock.core.live_count() == 1 for e in self.l1._objects.values()))
        self.finish_all(new_result)
        self.assertIn(old.sequence, self.module.jobs)

    def test_cancel_during_query_event_does_not_publish_slots_or_recreate_job(self):
        self.seed()
        handle = self.begin()
        self.ctx.event_bus.publish.side_effect = lambda event: self.module.abandon(handle)
        self.assertIsNone(self.module.query_owned(handle))
        self.assert_drained()

    def test_cancel_during_lookup_notification_is_deferred_until_registration(self):
        self.seed()
        def cancel(event):
            handle = next(iter(self.module.jobs.values())).handle
            self.module.abandon(handle)
        self.ctx.event_bus.publish.side_effect = cancel
        handle = self.begin()
        self.assertIsNone(self.module.query_owned(handle))
        self.assert_drained()

    def test_pending_child_error_is_visible_and_does_not_drop_initial_l1_tokens(self):
        self.seed(indices=[0])
        handle = self.begin()
        job = self.module.jobs[handle.sequence]
        child = self.storage.jobs[job.storage_handle.sequence]
        self.controller.jobs[child.downstream.sequence].error = "injected I/O failure"
        self.assertIsNone(self.module.query_owned(handle))
        self.assertIsNotNone(job.error)
        self.assertIn(job.storage_handle.sequence, self.storage.jobs)
        self.assertEqual(len(child.tokens), 1)

    def test_end_inside_lookup_event_removes_session_after_registration_once(self):
        self.seed()
        def end(event):
            self.module.end_owned(next(iter(self.module.jobs.values())).handle)
        self.ctx.event_bus.publish.side_effect = end
        handle = self.begin()
        self.assertIsNone(self.module.query_owned(handle))
        self.ctx.session_manager.remove.assert_called_once_with("r")
        self.assert_drained()

    def test_end_inside_query_event_is_deferred_and_releases_unclaimed_slots(self):
        self.seed()
        handle = self.begin()
        self.ctx.event_bus.publish.side_effect = lambda event: self.module.end_owned(handle)
        self.assertIsNone(self.module.query_owned(handle))
        self.ctx.session_manager.remove.assert_called_once_with("r")
        self.assert_drained()

    def test_old_end_after_new_job_drained_cannot_remove_new_session(self):
        self.seed()
        old = self.begin()
        result = self.module.query_owned(old)
        ticket = result.tickets[0]
        self.module.claim_retrieve(ticket)
        self.module.abandon(old)
        new = self.begin()
        self.finish_all(self.module.query_owned(new))
        self.ctx.session_manager.remove.reset_mock()
        self.module.end_owned(old)
        self.ctx.session_manager.remove.assert_not_called()
        self.assertTrue(self.module.finish_retrieve(ticket, succeeded=True))
        self.assert_drained()

    def test_real_end_with_session_touches_keys_without_anonymous_release(self):
        keys = self.seed()
        handle = self.begin()
        session = SimpleNamespace(lookup_ipc_key=self.key(), get_hashes=lambda group: [97, 98, 99])
        self.ctx.session_manager.remove.return_value = session
        with patch("owned_lookup_contract.LookupModule._chunk_major_object_keys", return_value=keys):
            self.assertTrue(self.module.end_owned(handle))
        self.assert_drained()

    def test_unsupported_aux_and_descriptor_topology_reject_before_reservation(self):
        for desc in (AttnWindowDesc([-1], group_kinds=("aux",)),
                     AttnWindowDesc([-1], world_size=2)):
            with self.subTest(desc=desc):
                self.ctx.layout_desc_registry.find_attn_desc.return_value = desc
                handle = self.begin(self.key(request_id=str(desc)))
                job = self.module.jobs[handle.sequence]
                self.assertIsNotNone(job.error)
                self.assertIsNone(job.storage_handle)
                self.assertFalse(self.storage.jobs)
                self.assertFalse(self.l1._objects)

    def test_corrupt_consumed_storage_completion_preserves_original_evidence(self):
        for fault in ("index", "duplicate", "identity", "missing"):
            with self.subTest(fault=fault):
                self.setUp()
                self.seed()
                handle = self.begin()
                job = self.module.jobs[handle.sequence]
                query = self.storage.query_owned
                def corrupt(child_handle):
                    result = query(child_handle)
                    if fault == "index":
                        return replace(result, retained_indices=(99,))
                    if fault == "duplicate":
                        return replace(result, reservations=result.reservations + result.reservations[:1])
                    if fault == "identity":
                        return replace(result, handle=JobHandle("foreign", 0, "r"))
                    return replace(result, reservations=result.reservations[:-1])
                self.storage.query_owned = corrupt
                self.assertIsNone(self.module.query_owned(handle))
                self.assertIsNotNone(job.error)
                self.assertIsNotNone(job.storage_completion)
                self.assertEqual(len(job.tokens), 2 if fault == "missing" else 3)
                self.assertFalse(self.storage.jobs)
                self.assertFalse(job.slots)

    def test_two_groups_preserve_each_window_in_worker_partition(self):
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1, 1])
        self.ctx.layout_desc_registry.find_group_layout_descs.return_value = {0: Mock(), 1: Mock()}
        keys = self.seed()
        handle = self.begin()
        result = self.module.query_owned(handle)
        self.assertEqual(result.hit_chunks, 3)
        claim = self.module.claim_retrieve(result.tickets[0])
        self.assertEqual({r.key for r in claim.reservations}, {keys[i] for i in (0, 2, 4, 5)})
        self.assertTrue(self.module.finish_retrieve(claim.ticket, succeeded=True))
        self.assert_drained()

    def test_reentrant_claim_for_other_job_is_blocked_during_query(self):
        self.seed()
        first = self.begin()
        result = self.module.query_owned(first)
        second = self.begin(self.key(request_id="other"))
        claims = []
        self.ctx.event_bus.publish.side_effect = lambda event: claims.append(
            self.module.claim_retrieve(result.tickets[0]))
        other = self.module.query_owned(second)
        self.assertEqual(claims, [None])
        self.ctx.event_bus.publish.side_effect = None
        self.finish_all(result)
        self.finish_all(other)
        self.assert_drained()

    def test_simultaneous_duplicate_terminal_acks_release_each_token_once(self):
        self.seed()
        handle = self.begin()
        result = self.module.query_owned(handle)
        ticket = result.tickets[0]
        self.module.claim_retrieve(ticket)
        job = self.module.jobs[handle.sequence]
        barrier = threading.Barrier(2)
        def finish():
            barrier.wait()
            return self.module.finish_retrieve(ticket, succeeded=True)
        with ThreadPoolExecutor(2) as pool:
            first, second = pool.submit(finish), pool.submit(finish)
            self.assertEqual(sorted((first.result(), second.result())), [False, True])
        self.assertEqual(len(job.release_results), 3)
        self.assertEqual(len({token_id(r.reservation) for r in job.release_results}), 3)
        self.assertEqual(self.module.closed, 1)
        self.assert_drained()

    def test_end_inside_pending_storage_query_advances_after_scope_exit(self):
        self.seed(indices=[0])
        handle = self.begin()
        child = self.stage_load(handle, [0, 1])
        query = self.storage.query_owned
        def end_then_query(storage_handle):
            self.module.end_owned(handle)
            return query(storage_handle)
        self.storage.query_owned = end_then_query
        self.assertIsNone(self.module.query_owned(handle))
        self.ctx.session_manager.remove.assert_called_once_with("r")
        self.assertTrue(self.storage.jobs[next(iter(self.storage.jobs))].abandoned)
        self.complete_load(child, [0, 1])
        self.assertTrue(self.module.advance(handle))
        self.assert_drained()

    def test_topology_mismatch_overlap_and_anonymous_paths_fail_closed(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.begin(self.key(readers=2))
        handle = self.begin()
        with self.assertRaises(RuntimeError):
            self.begin()
        result = self.module.query_owned(handle)
        for method in (self.module.lookup, self.module.query_prefetch_status,
                       self.module.query_prefetch_lookup_hits, self.module.wait_prefetch_status,
                       self.module.free_lookup_locks, self.module.end_session,
                       self.module.read_retrieve):
            with self.assertRaises(RuntimeError):
                method()
        with self.assertRaises(ValueError):
            self.module.finish_retrieve(result.tickets[0], succeeded=None)
        self.finish_all(result)
        self.assert_drained()


if __name__ == "__main__":
    unittest.main()
