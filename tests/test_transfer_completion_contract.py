"""Real LMCache futures with controlled events; these are CPU contracts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock

import test_leased_lookup_contract as lookup_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from transfer_completion_contract import TransferCompletionHarness
    from lmcache.v1.multiprocess.futures import MessagingFuture
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    TransferCompletionHarness = None


class ControlledEvents:
    def __init__(self):
        self.done = False
        self.error = None
        self.import_error = None
        self.imports = []
        self.synchronizations = 0

    def import_event(self, handle, device):
        if self.import_error:
            raise self.import_error
        self.imports.append((handle, device))
        return self

    def query_event(self, event):
        assert event is self
        if self.error:
            raise self.error
        return self.done

    def synchronize_event(self, event, device):
        self.synchronizations += 1
        if not self.done:
            raise RuntimeError("Test would block on device synchronization")


@unittest.skipUnless(TransferCompletionHarness, "Needs pinned LMCache/native extension")
class TransferCompletionTests(unittest.TestCase):
    key = lookup_fixture.LeasedLookupTests.key
    begin = lookup_fixture.LeasedLookupTests.begin
    bitmap = lookup_fixture.LeasedLookupTests.bitmap
    stage_load = lookup_fixture.LeasedLookupTests.stage_load
    complete_load = lookup_fixture.LeasedLookupTests.complete_load
    seed = lookup_fixture.LeasedLookupTests.seed
    ready = lookup_fixture.LeasedLookupTests.ready
    reset = lookup_fixture.LeasedLookupTests.reset
    assert_drained = lookup_fixture.LeasedLookupTests.assert_drained

    def setUp(self):
        lookup_fixture.LeasedLookupTests.setUp(self)
        self.transfers = TransferCompletionHarness(self.module)
        self.callbacks = {}

    def prepared(self, **kwargs):
        keys, job, tickets = self.ready(**kwargs)
        handles = tuple(self.transfers.prepare(t) for t in tickets)
        self.assertTrue(all(handles))
        return keys, job, handles

    def submit(self, handle, outcome=True):
        def enqueue(buffers, callback):
            self.assertTrue(buffers)
            self.callbacks[handle] = callback
            return outcome
        return self.transfers.enqueue(handle, enqueue)

    def state(self, handle):
        return self.transfers.states[handle.sequence]

    def assert_pinned(self, keys):
        self.assertTrue(all(not self.l1.is_key_evictable(k) for k in keys))
        self.assertTrue(all(bytes(self.l1._objects[k].memory_obj.data) == b"K" * 64
                            for k in keys))
        self.assertFalse(self.allocator.freed)

    def futures(self):
        raw, events = MessagingFuture(), ControlledEvents()
        device = raw.to_device_future(device="cpu-fixture", event_backend=events)
        return raw, events, device

    def test_rpc_then_device_event_then_callback_are_separate(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        self.submit(handle)
        raw, events, device = self.futures()
        self.assertEqual(self.transfers.observe_future(handle, device)[1], "pending")
        raw.set_result((b"event", True))
        self.assertEqual(self.transfers.observe_future(handle, raw)[1], "complete")
        self.assertEqual(self.transfers.observe_future(handle, device)[1], "pending")
        self.assert_pinned(keys)
        events.done = True
        self.assertEqual(self.transfers.observe_future(handle, device),
                         ("device", "complete", True))
        self.assert_pinned(keys)
        self.assertEqual(events.synchronizations, 0)
        self.assertEqual(len(events.imports), 1)
        self.assertTrue(self.callbacks[handle]())
        self.assertEqual(self.state(handle).phase, "closed")
        self.assertEqual(len(self.allocator.freed), 3)
        self.assert_drained()

    def test_false_response_with_inflight_event_retains_buffers(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        self.assertFalse(self.submit(handle, False))
        raw, events, device = self.futures()
        raw.set_result((b"event", False))
        self.assertFalse(device.query())
        self.assertEqual(self.transfers.observe_future(handle, device)[1], "pending")
        self.module.end_owned(job)
        self.assert_pinned(keys)
        events.done = True
        self.assertEqual(self.transfers.observe_future(handle, device)[2], False)
        self.assert_pinned(keys)
        self.assertTrue(self.callbacks[handle]())
        self.assertFalse(self.state(handle).succeeded)
        self.assert_drained()

    def test_timeout_rpc_and_event_errors_never_cleanup(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        self.submit(handle)
        raw, events, device = self.futures()
        self.assertFalse(device.wait(timeout=0))
        with self.assertRaises(TimeoutError):
            raw.result(timeout=0)
        raw.set_exception(RuntimeError("RPC failed after possible submission"))
        self.assertEqual(self.transfers.observe_future(handle, raw)[1], "error")
        self.assertEqual(self.transfers.observe_future(handle, device)[1], "error")
        raw2, events2, device2 = self.futures()
        raw2.set_result((b"event", True))
        events2.import_error = RuntimeError("import failed")
        self.assertEqual(self.transfers.observe_future(handle, device2)[1], "error")
        events2.import_error = None
        events2.error = RuntimeError("event query failed")
        self.assertEqual(self.transfers.observe_future(handle, device2)[1], "error")
        self.assert_pinned(keys)
        self.assertTrue(self.callbacks[handle]())
        self.assert_drained()

    def test_empty_event_response_does_not_authorize_server_cleanup(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        self.submit(handle, False)
        raw, events, device = self.futures()
        raw.set_result((b"", False))
        self.assertTrue(device.query())
        self.transfers.observe_future(handle, device)
        self.assert_pinned(keys)
        self.assertFalse(events.imports)
        self.assertTrue(self.callbacks[handle]())
        self.assert_drained()

    def test_reject_before_submit_and_callback_before_submit_are_distinct(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        self.assertFalse(self.transfers.stream_complete(handle))
        self.assertFalse(self.state(handle).stream_terminal)
        self.assertTrue(self.transfers.reject_unsubmitted(handle))
        submit = Mock()
        self.assertFalse(self.transfers.enqueue(handle, submit))
        submit.assert_not_called()
        self.assertFalse(self.transfers.reject_unsubmitted(handle))
        self.assert_drained()

    def test_partial_submit_then_exception_retains_until_callback(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        def partial(buffers, callback):
            self.callbacks[handle] = callback
            self.partial_buffers = buffers
            raise RuntimeError("Failure after first H2D submission")
        self.assertFalse(self.transfers.enqueue(handle, partial))
        self.assertEqual(self.state(handle).phase, "submission_unknown")
        self.assertFalse(self.transfers.reject_unsubmitted(handle))
        self.module.end_owned(job)
        self.reset(keys)
        self.l1.clear()
        self.assert_pinned(keys)
        self.assertEqual([bytes(b.data) for b in self.partial_buffers], [b"K" * 64] * 3)
        self.assertTrue(self.callbacks[handle]())
        self.assertEqual(len(self.allocator.freed), 3)
        self.assert_drained()

    def test_callback_submission_failure_keeps_evidence(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        def no_callback(buffers, callback):
            raise RuntimeError("Native callback registration failed after copy")
        self.assertFalse(self.transfers.enqueue(handle, no_callback))
        self.assertIn("registration failed", self.state(handle).errors[0])
        self.module.end_owned(job)
        self.assert_pinned(keys)
        # Represents a later SERVER stream observer, never a client future.
        self.assertTrue(self.transfers.stream_complete(handle))
        self.assert_drained()

    def test_reentrant_callback_waits_until_submitter_returns(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        def immediate(buffers, callback):
            self.assertTrue(callback())
            self.assertFalse(callback())
            self.assertEqual(self.state(handle).phase, "submitting")
            self.module.end_owned(job)
            self.assert_pinned(keys)
            return True
        self.assertTrue(self.transfers.enqueue(handle, immediate))
        self.assertEqual(self.state(handle).phase, "closed")
        self.assert_drained()

    def test_reentrant_callback_then_submission_exception_is_failed_terminal(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        def immediate(buffers, callback):
            callback()
            self.assert_pinned(keys)
            raise RuntimeError("Late submission failure")
        self.assertFalse(self.transfers.enqueue(handle, immediate))
        self.assertFalse(self.state(handle).succeeded)
        self.assertEqual(self.state(handle).phase, "closed")
        self.assertIn("Late submission failure", self.state(handle).errors[0])
        self.assert_drained()

    def test_invalid_receipt_retains_until_bound_completion(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        def invalid(buffers, callback):
            self.callbacks[handle] = callback
            return 1
        self.assertFalse(self.transfers.enqueue(handle, invalid))
        self.assert_pinned(keys)
        self.assertTrue(self.callbacks[handle]())
        self.assert_drained()

    def test_duplicate_and_forged_completion_do_not_free_sibling(self):
        keys, job, (a, b) = self.prepared(readers=2, temporary=True)
        self.submit(a)
        self.submit(b)
        for forged in (None, replace(a, registry="old-server"),
                       replace(a, sequence=b.sequence), replace(a, ticket=b.ticket)):
            with self.subTest(forged=forged):
                self.assertFalse(self.transfers.stream_complete(forged))
        self.assertTrue(self.callbacks[a]())
        self.assertFalse(self.callbacks[a]())
        self.assertFalse(self.transfers.prepare(a.ticket))
        self.assert_pinned(keys)
        self.assertTrue(self.callbacks[b]())
        self.assert_drained()

    def test_old_callback_cannot_free_new_lookup_generation(self):
        keys, job, (old,) = self.prepared(temporary=True)
        self.submit(old)
        self.module.end_owned(job)
        self.reset(keys)
        job2 = self.begin()
        ticket2, = self.module.query_owned(job2).tickets
        new = self.transfers.prepare(ticket2)
        self.submit(new)
        self.assertTrue(self.callbacks[old]())
        self.assertFalse(self.callbacks[old]())
        self.assert_pinned(keys)
        self.assertTrue(self.callbacks[new]())
        self.assert_drained()

    def test_cleanup_failure_is_not_retried_and_sibling_can_finish(self):
        keys, job, (a, b) = self.prepared(readers=2, temporary=True)
        self.submit(a)
        self.submit(b)
        finish = self.module.finish_retrieve
        calls = []
        def fail_once(ticket, **kwargs):
            calls.append(ticket)
            if ticket == a.ticket:
                raise RuntimeError("Unknown cleanup outcome")
            return finish(ticket, **kwargs)
        self.module.finish_retrieve = fail_once
        self.assertFalse(self.callbacks[a]())
        self.assertTrue(self.state(a).cleanup_attempted)
        self.assertFalse(self.callbacks[a]())
        self.assertEqual(calls, [a.ticket])
        self.assertTrue(self.callbacks[b]())
        self.assertEqual(self.state(b).phase, "closed")
        self.assert_pinned(keys)
        self.assertTrue(self.module.accesses)

    def test_concurrent_callback_is_once_only(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        self.submit(handle)
        barrier = threading.Barrier(8)
        def complete(_):
            barrier.wait()
            return self.callbacks[handle]()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(complete, range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(self.allocator.freed), 3)
        self.assert_drained()

    def test_callback_end_and_submit_return_race(self):
        for _ in range(30):
            self.setUp()
            keys, job, (handle,) = self.prepared(temporary=True)
            barrier = threading.Barrier(3)
            def submitter(buffers, callback):
                self.callbacks[handle] = callback
                barrier.wait()
                return True
            def complete():
                barrier.wait()
                return self.callbacks[handle]()
            def end():
                barrier.wait()
                return self.module.end_owned(job)
            with ThreadPoolExecutor(max_workers=3) as pool:
                submitting = pool.submit(self.transfers.enqueue, handle, submitter)
                completed = pool.submit(complete)
                ended = pool.submit(end)
                self.assertTrue(submitting.result(timeout=3))
                self.assertTrue(completed.result(timeout=3))
                self.assertTrue(ended.result(timeout=3))
            self.assertEqual(self.state(handle).phase, "closed")
            self.assertEqual(len(self.allocator.freed), 3)
            self.assert_drained()

    def test_duplicate_enqueue_and_reject_during_submit_are_denied(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        other = Mock()
        def running(buffers, callback):
            self.assertFalse(self.transfers.reject_unsubmitted(handle))
            self.assertFalse(self.transfers.enqueue(handle, other))
            self.assertIsNone(self.transfers.prepare(handle.ticket))
            self.callbacks[handle] = callback
            return True
        self.assertTrue(self.transfers.enqueue(handle, running))
        other.assert_not_called()
        self.assertTrue(self.callbacks[handle]())
        self.assert_drained()

    def test_failed_preparation_can_terminate_without_submission(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        read = self.module.read_retrieve
        def end_before_delivery(t):
            self.module.end_owned(job)
            return read(t)
        self.module.read_retrieve = end_before_delivery
        handle = self.transfers.prepare(ticket)
        self.assertEqual(self.state(handle).phase, "preparation_failed")
        submit = Mock()
        self.assertFalse(self.transfers.enqueue(handle, submit))
        submit.assert_not_called()
        self.assertTrue(self.transfers.reject_unsubmitted(handle))
        self.assert_drained()

    def test_invalid_ticket_and_unknown_acquisition_retain_no_submit_evidence(self):
        self.assertIsNone(self.transfers.prepare([]))
        keys, job, (ticket,) = self.ready(temporary=True)
        claim = self.module.claim_retrieve
        def uncertain(t):
            claim(t)
            raise RuntimeError("Claim result lost")
        self.module.claim_retrieve = uncertain
        handle = self.transfers.prepare(ticket)
        self.assertEqual(self.state(handle).phase, "acquisition_unknown")
        self.assertFalse(self.transfers.reject_unsubmitted(handle))
        self.assertFalse(self.transfers.stream_complete(handle))
        self.assertIsNone(self.transfers.prepare(ticket))
        self.assertTrue(self.module.jobs[job.sequence].tokens)

    def test_declined_claim_can_be_retried_after_processing_scope(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        owner = self.module.jobs[job.sequence]
        owner.processing = True
        self.assertIsNone(self.transfers.prepare(ticket))
        owner.processing = False
        handle = self.transfers.prepare(ticket)
        self.assertIsNotNone(handle)
        self.assertTrue(self.transfers.reject_unsubmitted(handle))
        self.assert_drained()

    def test_end_before_enqueue_rejects_without_calling_submitter(self):
        keys, job, (handle,) = self.prepared(temporary=True)
        self.module.end_owned(job)
        submit = Mock()
        self.assertFalse(self.transfers.enqueue(handle, submit))
        submit.assert_not_called()
        self.assertEqual(self.state(handle).phase, "closed")
        self.assertFalse(self.state(handle).stream_terminal)
        self.assert_drained()

    def test_real_partial_unpin_failure_does_not_retry_or_block_sibling(self):
        keys, job, tickets = self.ready(readers=2)
        core = self.l1._objects[keys[1]].read_lock.core
        proxy = Mock(wraps=core)
        self.l1._objects[keys[1]].read_lock.core = proxy
        a, b = [self.transfers.prepare(t) for t in tickets]
        self.submit(a)
        self.submit(b)
        proxy.unpin.side_effect = RuntimeError("Unpin result unknown")
        self.assertFalse(self.callbacks[a]())
        self.assertFalse(self.callbacks[a]())
        proxy.unpin.assert_called_once()
        self.assertEqual(self.state(a).phase, "terminal")
        self.assertTrue(self.state(a).stream_terminal)
        access = self.module.accesses[a.ticket]
        self.assertEqual(len(self.l1._lease(access.lease.handle).pins), 1)
        proxy.unpin.side_effect = None
        self.assertTrue(self.callbacks[b]())
        self.assertEqual(proxy.unpin.call_count, 2)
        self.assertEqual(core.active_count(), 1)
        self.assertIn(a.ticket, self.module.accesses)
        self.assertNotIn(b.ticket, self.module.accesses)

    def test_ttl_and_end_cannot_free_buffer_before_stream_completion(self):
        self.l1._reservation_ttl_ms = 30
        keys, job, (handle,) = self.prepared(temporary=True)
        self.submit(handle)
        time.sleep(0.06)
        self.assertTrue(all(self.l1._objects[k].read_lock.core.live_count() == 0
                            for k in keys))
        self.module.end_owned(job)
        self.l1.clear()
        self.assert_pinned(keys)
        result = self.l1.reserve_write(keys, [False] * len(keys), Mock())
        self.assertTrue(all(result[k][0] != L1Error.SUCCESS for k in keys))
        self.assertTrue(self.callbacks[handle]())
        self.assertEqual(self.module.expired, 3)
        self.assertEqual(len(self.allocator.freed), 3)
        self.assert_drained()
