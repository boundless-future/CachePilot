"""Lookup slots own real CPU buffers until explicit consumer termination."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock

import test_owned_lookup_contract as lookup_fixture
import test_leased_l1_contract as l1_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from leased_lookup_contract import LeasedLookupHarness
    from leased_l1_contract import LeasedL1Harness
    from owned_lookup_contract import Worker
    from owned_prefetch_contract import JobHandle
    from lmcache.v1.distributed.api import AttnWindowDesc, ObjectKey
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    LeasedLookupHarness = None


@unittest.skipUnless(LeasedLookupHarness, "Needs native extension and pinned LMCache")
class LeasedLookupTests(unittest.TestCase):
    # Reuse setup helpers, without inheriting/collecting the earlier tests.
    key = lookup_fixture.OwnedLookupTests.key
    begin = lookup_fixture.OwnedLookupTests.begin
    bitmap = lookup_fixture.OwnedLookupTests.bitmap
    stage_load = lookup_fixture.OwnedLookupTests.stage_load
    complete_load = lookup_fixture.OwnedLookupTests.complete_load

    def setUp(self):
        lookup_fixture.OwnedLookupTests.setUp(self)
        self.allocator = l1_fixture.ReusingAllocator()
        self.l1 = LeasedL1Harness(self.allocator, Mock())
        self.controller = lookup_fixture.OwnedPrefetchHarness(
            self.l1, Mock(), adapters={0: self.adapter},
            descriptors={0: lookup_fixture.SimpleNamespace(type_name="CPU-fixture")})
        self.storage = lookup_fixture.OwnedStorageHarness(self.l1, self.controller, Mock())
        self.module = LeasedLookupHarness(self.ctx, self.storage)

    def seed(self, key=None, indices=None, temporary=False):
        key = key or self.key()
        keys = self.module._chunk_major_object_keys(key, self.hashes)
        for i in range(len(keys)) if indices is None else indices:
            result = self.l1.reserve_write([keys[i]], [temporary], Mock())
            self.assertEqual(result[keys[i]][0], L1Error.SUCCESS)
            self.l1._objects[keys[i]].memory_obj.data[:] = b"K" * 64
            self.l1.finish_write([keys[i]])
        return keys

    def ready(self, *, readers=1, temporary=False, world_size=1):
        key = self.key(readers=readers, world_size=world_size)
        keys = self.seed(key, temporary=temporary)
        workers = tuple(Worker(f"worker-{r}-{i}", r)
                        for r in range(world_size) for i in range(readers))
        handle = self.begin(key, workers)
        completion = self.module.query_owned(handle)
        self.assertIsNotNone(completion)
        return keys, handle, completion.tickets

    def read(self, ticket):
        self.assertIsNotNone(self.module.claim_retrieve(ticket))
        result = self.module.read_retrieve(ticket)
        self.assertIsNone(result.error)
        self.assertTrue(result.buffers)
        return result

    def finish(self, ticket, succeeded=True):
        return self.module.finish_retrieve(ticket, succeeded=succeeded, terminal=True)

    def assert_drained(self):
        lookup_fixture.OwnedLookupTests.assert_drained(self)
        self.assertFalse(self.module.accesses)
        self.assertFalse(self.l1.leases)

    def reset(self, keys):
        for key in keys:
            self.l1._objects[key].read_lock.core.reset()

    def test_success_orders_unpin_before_token_release_and_frees_once(self):
        keys, handle, (ticket,) = self.ready(temporary=True)
        result = self.read(ticket)
        job = self.module.jobs[handle.sequence]
        release = self.l1.finish_read_owned
        observed = []
        def check(reservations):
            self.assertTrue(self.l1._lock._is_owned())
            observed.extend(self.l1._objects[r.key].read_lock.core.active_count()
                            for r in reservations)
            return release(reservations)
        self.l1.finish_read_owned = check
        self.assertEqual([bytes(b.data) for b in result.buffers], [b"K" * 64] * 3)
        self.assertTrue(self.finish(ticket))
        self.assertEqual(observed, [0, 0, 0])
        self.assertEqual([r.status for r in job.release_results], ["released"] * 3)
        self.assertEqual(len(self.allocator.freed), 3)
        self.assertFalse(self.finish(ticket))
        self.assert_drained()

    def test_delivery_claim_identity_and_explicit_terminal_are_required(self):
        keys, handle, (ticket,) = self.ready()
        self.assertIsNone(self.module.read_retrieve(ticket))
        for bad in (None, replace(ticket, worker=Worker("restarted", 0)),
                    replace(ticket, lookup=JobHandle("foreign", handle.sequence, "r"))):
            with self.subTest(bad=bad):
                self.assertIsNone(self.module.claim_retrieve(bad))
                self.assertIsNone(self.module.read_retrieve(bad))
                self.assertFalse(self.finish(bad, False))
        self.module.claim_retrieve(ticket)
        with self.assertRaises(ValueError):
            self.finish(ticket)
        result = self.module.read_retrieve(ticket)
        self.assertIsNone(self.module.read_retrieve(ticket))
        self.assertIsNone(self.module.claim_retrieve(ticket))
        for terminal in (False, None, 1):
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                self.module.finish_retrieve(ticket, succeeded=True, terminal=terminal)
        with self.assertRaises(ValueError):
            self.module.finish_retrieve(ticket, succeeded=1, terminal=True)
        self.assertEqual(len(result.buffers), 3)
        self.assertTrue(all(self.l1._objects[k].read_lock.core.active_count() == 1 for k in keys))
        self.assertTrue(self.finish(ticket))
        self.assert_drained()

    def test_end_between_claim_and_read_requires_failed_terminal(self):
        keys, handle, (ticket,) = self.ready()
        self.module.claim_retrieve(ticket)
        self.assertTrue(self.module.end_owned(handle))
        self.assertIsNone(self.module.read_retrieve(ticket))
        self.assertTrue(all(self.l1._objects[k].read_lock.core.live_count() == 1 for k in keys))
        self.assertTrue(self.finish(ticket, False))
        self.assert_drained()

    def test_end_and_ttl_during_cpu_future_wait_for_actual_consumption(self):
        self.l1._reservation_ttl_ms = 30
        keys, handle, (ticket,) = self.ready(temporary=True)
        result = self.read(ticket)
        entered, proceed = threading.Event(), threading.Event()
        def consume():
            entered.set()
            self.assertTrue(proceed.wait(5))
            return tuple(bytes(b.data) for b in result.buffers)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(consume)
            try:
                self.assertTrue(entered.wait(5))
                self.module.end_owned(handle)
                time.sleep(0.06)
                for key in keys:
                    self.assertEqual(self.l1.delete([key])[key], L1Error.KEY_IS_LOCKED)
                self.l1.clear()
                self.assertFalse(self.allocator.freed)
                self.assertFalse(future.done())
            finally:
                proceed.set()
            self.assertEqual(future.result(), (b"K" * 64,) * 3)
        job = self.module.jobs[handle.sequence]
        self.assertTrue(self.finish(ticket))
        self.assertEqual([r.status for r in job.release_results], ["stale_epoch"] * 3)
        self.assertEqual(self.module.expired, 3)
        self.assertEqual(len(self.allocator.freed), 3)
        self.assert_drained()

    def test_reset_and_new_generation_isolate_old_terminal(self):
        keys, old, (ticket,) = self.ready()
        self.read(ticket)
        self.module.end_owned(old)
        self.reset(keys)
        new = self.begin()
        completion = self.module.query_owned(new)
        new_ticket, = completion.tickets
        self.read(new_ticket)
        old_job = self.module.jobs[old.sequence]
        self.assertTrue(self.finish(ticket))
        self.assertEqual([r.status for r in old_job.release_results], ["stale_epoch"] * 3)
        self.assertTrue(all(self.l1._objects[k].read_lock.core.active_count() == 1 for k in keys))
        self.assertFalse(self.finish(ticket))
        self.assertTrue(self.finish(new_ticket))
        self.assert_drained()

    def test_shared_temporary_readers_hold_bytes_until_last_terminal(self):
        keys, handle, tickets = self.ready(readers=2, temporary=True)
        a, b = [self.read(t) for t in tickets]
        self.assertTrue(all(x is y for x, y in zip(a.buffers, b.buffers)))
        self.reset(keys)
        self.module.end_owned(handle)
        self.assertTrue(self.finish(tickets[0], False))
        self.assertFalse(self.allocator.freed)
        self.assertEqual([bytes(x.data) for x in b.buffers], [b"K" * 64] * 3)
        self.assertTrue(self.finish(tickets[1]))
        self.assertEqual(len(self.allocator.freed), 3)
        self.assertEqual(self.module.expired, 6)
        self.assert_drained()

    def test_tp_workers_only_pin_their_shard(self):
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1], world_size=2)
        keys, handle, tickets = self.ready(world_size=2)
        for ticket in tickets:
            result = self.read(ticket)
            slot = self.module.jobs[handle.sequence].slots[ticket]
            rank = ObjectKey.ComputeKVRank(2, ticket.worker.rank, 2, ticket.worker.rank)
            self.assertEqual({r.key.kv_rank for r in slot.reservations}, {rank})
            self.assertEqual(len(result.buffers), 3)
        for ticket in tickets:
            self.assertTrue(self.finish(ticket))
        self.assert_drained()

    def test_stale_before_read_has_no_buffers_and_failed_terminal_retires_stale(self):
        keys, handle, (ticket,) = self.ready()
        self.module.claim_retrieve(ticket)
        self.reset(keys)
        result = self.module.read_retrieve(ticket)
        self.assertFalse(result.buffers)
        self.assertIn("stale_epoch", result.error)
        with self.assertRaises(ValueError):
            self.finish(ticket)
        self.assertTrue(self.finish(ticket, False))
        self.assertEqual(self.module.expired, 3)
        self.assert_drained()

    def test_end_of_expired_offered_slots_observes_stale_without_buffers(self):
        keys, handle, tickets = self.ready(readers=2)
        self.reset(keys)
        self.assertTrue(self.module.end_owned(handle))
        self.assertEqual(self.module.expired, 6)
        self.assert_drained()

    def test_failed_batch_rolls_back_but_missing_token_owner_stays_visible(self):
        keys, handle, (ticket,) = self.ready()
        self.module.claim_retrieve(ticket)
        missing = self.l1._objects.pop(keys[1])  # Fault injection outside public API.
        result = self.module.read_retrieve(ticket)
        self.assertFalse(result.buffers)
        self.assertIsNotNone(result.error)
        self.assertFalse(self.l1.leases)
        self.assertEqual(self.l1._objects[keys[0]].read_lock.core.active_count(), 0)
        self.assertFalse(self.finish(ticket, False))
        job = self.module.jobs[handle.sequence]
        self.assertEqual([r.status for r in job.release_results], ["released", "object_gone"])
        self.assertEqual(len(job.tokens), 2)
        self.assertIsNotNone(job.error)
        self.assertFalse(self.finish(ticket, False))
        self.assertEqual(missing.read_lock.core.live_count(), 1)

    def test_rollback_uncertainty_keeps_pin_and_never_retries(self):
        keys, handle, (ticket,) = self.ready()
        self.module.claim_retrieve(ticket)
        first = self.l1._objects[keys[0]]
        proxy = Mock(wraps=first.read_lock.core)
        first.read_lock.core = proxy
        proxy.unpin.side_effect = RuntimeError("rollback uncertain")
        self.l1._objects[keys[1]].write_lock.lock()
        result = self.module.read_retrieve(ticket)
        self.assertFalse(result.buffers)
        self.assertIn("rollback uncertain", result.error)
        self.assertFalse(self.finish(ticket, False))
        access = self.module.accesses[ticket]
        self.assertEqual(len(self.l1._lease(access.lease.handle).pins), 1)
        self.assertFalse(access.lease_finished)
        self.assertFalse(self.finish(ticket, False))
        proxy.unpin.assert_called_once()

    def test_partial_unpin_failure_does_not_block_independent_worker_terminal(self):
        keys, handle, tickets = self.ready(readers=2)
        entry = self.l1._objects[keys[1]]
        core = entry.read_lock.core
        proxy = Mock(wraps=core)
        entry.read_lock.core = proxy
        for ticket in tickets:
            self.read(ticket)
        proxy.unpin.side_effect = RuntimeError("worker one unpin uncertain")
        self.assertFalse(self.finish(tickets[0]))
        failed_access = self.module.accesses[tickets[0]]
        state = self.l1._lease(failed_access.lease.handle)
        self.assertEqual(len(state.pins), 1)
        self.assertEqual(len(state.results), 2)
        proxy.unpin.side_effect = None
        self.assertTrue(self.finish(tickets[1]))
        job = self.module.jobs[handle.sequence]
        self.assertIn("worker one", job.error)
        self.assertEqual(job.slots[tickets[1]].state, "closed")
        self.assertEqual(len(job.tokens), 3)
        self.assertEqual(core.active_count(), 1)
        self.assertFalse(self.finish(tickets[0]))
        self.assertEqual(proxy.unpin.call_count, 2)
        self.assertIn(tickets[0], self.module.accesses)

    def test_partial_token_failure_keeps_lease_terminal_and_remaining_tokens(self):
        keys, handle, (ticket,) = self.ready()
        self.read(ticket)
        self.l1._objects[keys[1]].write_lock.lock()  # Outside normal API.
        self.assertFalse(self.finish(ticket))
        job = self.module.jobs[handle.sequence]
        self.assertEqual([r.status for r in job.release_results], ["released", "write_locked"])
        self.assertEqual(len(job.tokens), 2)
        self.assertTrue(self.module.accesses[ticket].lease_finished)
        self.assertFalse(self.l1.leases)
        self.assertFalse(self.finish(ticket))
        self.assertEqual(len(job.release_results), 2)

    def test_unknown_acquisition_result_retains_both_registries(self):
        keys, handle, (ticket,) = self.ready()
        self.module.claim_retrieve(ticket)
        acquire = self.l1.begin_read_owned
        def lose_receipt(reservations):
            acquire(reservations)
            raise RuntimeError("receipt delivery unknown")
        self.l1.begin_read_owned = lose_receipt
        result = self.module.read_retrieve(ticket)
        self.assertFalse(result.buffers)
        self.assertIn("receipt delivery unknown", result.error)
        self.assertEqual(len(self.l1.leases), 1)
        self.assertFalse(self.finish(ticket, False))
        self.assertIsNone(self.module.accesses[ticket].lease)
        job = self.module.jobs[handle.sequence]
        self.assertIn("receipt delivery unknown", job.error)
        self.assertIn("Unknown L1 acquisition", job.error)
        self.assertEqual(len(job.tokens), 3)

    def test_cleanup_free_or_notification_failure_retains_evidence(self):
        for fault in ("free", "notify"):
            with self.subTest(fault=fault):
                self.setUp()
                keys, handle, (ticket,) = self.ready(temporary=True)
                self.read(ticket)
                self.reset(keys)
                if fault == "free":
                    self.allocator.free_error = RuntimeError("free uncertain")
                else:
                    self.l1._event_bus.publish.side_effect = RuntimeError("notification uncertain")
                self.assertFalse(self.finish(ticket))
                access = self.module.accesses[ticket]
                state = self.l1._lease(access.lease.handle)
                self.assertFalse(state.pins)
                self.assertIsNotNone(state.error)
                self.assertEqual(len(self.module.jobs[handle.sequence].tokens), 3)
                self.assertFalse(self.finish(ticket))
                self.assertEqual(len(self.allocator.freed), 1)

    def test_reentrant_end_during_pin_does_not_deliver_cancelled_buffers(self):
        keys, handle, (ticket,) = self.ready()
        self.module.claim_retrieve(ticket)
        entry = self.l1._objects[keys[0]]
        core = entry.read_lock.core
        proxy = Mock(wraps=core)
        def pin(token):
            self.module.end_owned(handle)
            return core.pin(token)
        proxy.pin.side_effect = pin
        entry.read_lock.core = proxy
        result = self.module.read_retrieve(ticket)
        self.assertFalse(result.buffers)
        self.assertIn("Cancelled", result.error)
        self.assertTrue(all(self.l1._objects[k].read_lock.core.active_count() == 1 for k in keys))
        self.ctx.session_manager.remove.assert_called_once_with("r")
        self.assertTrue(self.finish(ticket, False))
        self.assert_drained()

    def test_reentrant_end_during_terminal_does_not_repeat_release(self):
        keys, handle, tickets = self.ready(readers=2, temporary=True)
        self.read(tickets[0])
        entry = self.l1._objects[keys[0]]
        # The already captured native core remains the lease owner.
        release = self.l1.finish_read_owned
        def end_then_release(reservations):
            self.module.end_owned(handle)
            return release(reservations)
        self.l1.finish_read_owned = end_then_release
        job = self.module.jobs[handle.sequence]
        self.assertTrue(self.finish(tickets[0]))
        self.assertEqual(len(job.release_results), 6)
        self.assertEqual(len(self.allocator.freed), 3)
        self.assertIsNone(self.module.claim_retrieve(tickets[1]))
        self.assert_drained()

    def test_reentrant_replacement_consults_original_core_not_new_object(self):
        keys, handle, (ticket,) = self.ready(temporary=True)
        result = self.read(ticket)
        self.reset(keys)
        listener = Mock()
        fresh = []
        def replace_later(deleted):
            if deleted == [keys[0]]:
                self.assertEqual(self.l1.delete([keys[1]])[keys[1]], L1Error.SUCCESS)
                self.l1.reserve_write([keys[1]], [False], Mock())
                self.l1.finish_write([keys[1]])
                fresh.extend(self.l1.reserve_read_owned([keys[1]]).reservations)
        listener.on_l1_keys_deleted_by_manager.side_effect = replace_later
        self.l1._registered_listeners.append(listener)
        job = self.module.jobs[handle.sequence]
        self.assertTrue(self.finish(ticket))
        self.assertEqual([r.status for r in job.release_results], ["stale_epoch"] * 3)
        self.assertEqual(self.l1._objects[keys[1]].read_lock.core.live_count(), 1)
        self.assertIs(self.l1._objects[keys[1]].memory_obj, result.buffers[1])
        self.l1.finish_read_owned(fresh)
        self.assert_drained()

    def test_unexpected_object_replacement_never_claims_recovered_job(self):
        keys, handle, (ticket,) = self.ready()
        self.read(ticket)
        original = self.l1._objects.pop(keys[0])  # Invalid external mutation.
        self.l1.reserve_write([keys[0]], [False], Mock())
        self.l1.finish_write([keys[0]])
        fresh = self.l1.reserve_read_owned([keys[0]])
        self.assertFalse(self.finish(ticket))
        job = self.module.jobs[handle.sequence]
        self.assertEqual(job.release_results[0].status, "released")
        self.assertIn("Original object changed", job.release_results[0].error)
        self.assertEqual(len(job.tokens), 2)  # Known consumption, unresolved job.
        self.assertEqual(original.read_lock.core.active_count(), 0)
        self.assertEqual(self.l1._objects[keys[0]].read_lock.core.live_count(), 1)
        self.assertFalse(self.finish(ticket))
        self.l1.finish_read_owned(fresh.reservations)

    def test_duplicate_terminal_race_consumes_one_lease(self):
        keys, handle, (ticket,) = self.ready(temporary=True)
        self.read(ticket)
        job = self.module.jobs[handle.sequence]
        barrier = threading.Barrier(8)
        def finish(_):
            barrier.wait()
            return self.finish(ticket)
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(finish, range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(job.release_results), 3)
        self.assertEqual(len(self.allocator.freed), 3)
        self.assert_drained()

    def test_read_end_race_delivers_only_with_retained_lease(self):
        self.seed()
        with ThreadPoolExecutor(2) as pool:
            for _ in range(30):
                handle = self.begin()
                ticket, = self.module.query_owned(handle).tickets
                self.module.claim_retrieve(ticket)
                barrier = threading.Barrier(2)
                def read():
                    barrier.wait()
                    return self.module.read_retrieve(ticket)
                def end():
                    barrier.wait()
                    return self.module.end_owned(handle)
                reading, ending = pool.submit(read), pool.submit(end)
                result = reading.result()
                self.assertTrue(ending.result())
                if result is not None:
                    self.assertTrue(result.buffers)
                    self.assertTrue(all(e.read_lock.core.active_count() == 1
                                        for e in self.l1._objects.values()))
                self.assertTrue(self.finish(ticket, bool(result and result.buffers)))
                self.assert_drained()

    def test_read_failed_terminal_race_never_delivers_after_closed_slot(self):
        self.seed()
        with ThreadPoolExecutor(2) as pool:
            for _ in range(30):
                handle = self.begin()
                ticket, = self.module.query_owned(handle).tickets
                self.module.claim_retrieve(ticket)
                barrier = threading.Barrier(2)
                delivered, consumed = threading.Event(), threading.Event()
                def read():
                    barrier.wait()
                    result = self.module.read_retrieve(ticket)
                    if result is not None:
                        # Consumer confirms stopped access before terminal.
                        self.assertEqual([bytes(b.data) for b in result.buffers], [b"K" * 64] * 3)
                        consumed.set()
                    delivered.set()
                    return result
                def finish():
                    barrier.wait()
                    # Acquire gate to decide if delivery is already in progress.
                    with self.module.gate:
                        active = ticket in self.module.accesses
                        if not active:
                            return self.finish(ticket, False)
                    self.assertTrue(delivered.wait(5))
                    self.assertTrue(consumed.is_set())
                    return self.finish(ticket, False)
                r, f = pool.submit(read), pool.submit(finish)
                result, finished = r.result(), f.result()
                self.assertTrue(finished)
                if result is None:
                    self.assertFalse(consumed.is_set())
                self.assert_drained()

    def test_real_storage_l2_completion_delivers_temporary_cpu_buffers(self):
        self.seed(indices=[0])
        handle = self.begin()
        child = self.stage_load(handle, [0, 1])
        self.complete_load(child, [0, 1])
        ticket, = self.module.query_owned(handle).tickets
        result = self.read(ticket)
        self.assertEqual([bytes(b.data) for b in result.buffers], [b"K" * 64, b"N" * 64, b"N" * 64])
        self.assertTrue(self.finish(ticket))
        self.assertEqual(len(self.allocator.freed), 2)
        self.assert_drained()


if __name__ == "__main__":
    unittest.main()
