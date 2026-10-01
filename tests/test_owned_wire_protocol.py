"""Version/liveness gates before the original reader slot is claimed."""
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

import test_transfer_plan_contract as fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    import msgspec
    from owned_wire_protocol import (OwnedSessionRuntime, QueryReplyV1, RetrieveV1,
                                     TicketV1, decode_retrieve)
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    OwnedSessionRuntime = None


@unittest.skipUnless(OwnedSessionRuntime, "Needs pinned LMCache/native extension")
class WireTests(unittest.TestCase):
    setUp = fixture.TransferPlanTests.setUp
    key = fixture.TransferPlanTests.key
    begin = fixture.TransferPlanTests.begin
    bitmap = fixture.TransferPlanTests.bitmap
    stage_load = fixture.TransferPlanTests.stage_load
    complete_load = fixture.TransferPlanTests.complete_load
    seed = fixture.TransferPlanTests.seed
    ready = fixture.TransferPlanTests.ready
    assert_drained = fixture.TransferPlanTests.assert_drained
    request_key = fixture.TransferPlanTests.request_key

    def runtime(self):
        runtime = OwnedSessionRuntime(self.module, self.transfers)
        runtime.register(self.layout.worker)
        return runtime

    def packet(self, original_ticket, **changes):
        args = dict(version=1, ticket=TicketV1.capture(original_ticket), registration="registration-1",
                    block_ids=((0, 1, 2, 3, 4, 5),), start=0, end=768)
        args.update(changes)
        return msgspec.msgpack.encode(args)

    def test_query_to_retrieve_versioned_roundtrip(self):
        runtime = self.runtime()
        self.seed(temporary=True)
        job = runtime.begin(self.key(), (self.layout.worker,))
        wire = runtime.query(job)
        reply = msgspec.msgpack.decode(wire, type=QueryReplyV1)
        self.assertEqual(reply.hit_chunks, 3)
        ticket, = (t.original() for t in reply.tickets)
        handle = runtime.retrieve(self.packet(ticket), self.request_key())
        self.assertEqual(handle.ticket, ticket)
        self.transfers.reject_unsubmitted(handle)
        self.assert_drained()

    def test_unknown_version_fields_boolean_and_missing_identity_rejected(self):
        runtime = self.runtime()
        _, job, (ticket,) = self.ready(temporary=True)
        bad = (dict(version=2), dict(version=True), dict(end=True),
               dict(block_ids=((0, 1, 2, 3, 4, True),)), dict(extra="unknown"),
               dict(ticket=dict(server="x", sequence=0, request_id="r", worker="w")))
        for change in bad:
            with self.subTest(change=change), self.assertRaises((msgspec.ValidationError, ValueError)):
                runtime.retrieve(self.packet(ticket, **change), self.request_key())
            self.assertFalse(self.module.accesses)
        self.module.end_owned(job)
        self.assert_drained()

    def test_no_end_disconnect_and_late_lookup(self):
        runtime = self.runtime()
        _, _, (ticket,) = self.ready(temporary=True)
        runtime.disconnect(ticket.worker)
        self.assert_drained()
        with self.assertRaises(ValueError):
            runtime.begin(self.key(), (ticket.worker,))
        with self.assertRaises(ValueError):
            runtime.retrieve(self.packet(ticket), self.request_key())
        with self.assertRaises(ValueError):
            runtime.register(ticket.worker)

    def test_disconnect_running_reader_retains_until_terminal(self):
        runtime = self.runtime()
        _, _, (ticket,) = self.ready(temporary=True)
        handle = runtime.retrieve(self.packet(ticket), self.request_key())
        callbacks = []
        self.transfers.enqueue_plan(handle, lambda plan, cb: (callbacks.append(cb) or True))
        runtime.disconnect(ticket.worker)
        self.assertFalse(runtime.shutdown()['drained'])
        self.assertFalse(self.allocator.freed)
        callbacks[0]()
        self.assertTrue(runtime.shutdown()['drained'])
        self.assert_drained()

    def test_shutdown_rejects_new_work(self):
        runtime = self.runtime()
        self.assertTrue(runtime.shutdown()['drained'])
        with self.assertRaises(ValueError):
            runtime.begin(self.key(), (self.layout.worker,))

    def test_unfinished_controller_is_retained_after_shutdown(self):
        runtime = self.runtime()
        # No completed adapter load; a timeout is not an I/O terminal signal.
        job = runtime.begin(self.key(), (self.layout.worker,))
        self.assertIsNone(runtime.query(job))
        result = runtime.shutdown()
        self.assertFalse(result['drained'])
        self.assertIn(job.sequence, self.module.jobs)
        self.assertFalse(self.allocator.freed)

    def socket_rejection(self, raises):
        import zmq
        from owned_socket_runtime import OwnedSocketEndpoint, QueryV1, TransferV1, ReplyV1
        runtime = self.runtime()
        self.seed(temporary=True)
        job = runtime.begin(self.key(), (self.layout.worker,))
        def submit(*args):
            if raises:
                raise ValueError('target validation failed before enqueue')
            return False
        endpoint = OwnedSocketEndpoint(runtime, SimpleNamespace(submit=submit),
            self.layout.worker, job, self.request_key(), None, None, None)
        context = zmq.Context()
        client = context.socket(zmq.REQ)
        client.setsockopt(zmq.LINGER, 0)
        client.setsockopt(zmq.RCVTIMEO, 5000)
        client.connect(endpoint.address)
        try:
            client.send(msgspec.msgpack.encode(QueryV1(1, job.server, job.sequence, job.external_request_id)))
            reply = msgspec.msgpack.decode(client.recv(), type=ReplyV1)
            ticket = msgspec.msgpack.decode(reply.data, type=QueryReplyV1).tickets[0].original()
            client.send(msgspec.msgpack.encode(TransferV1(1, self.packet(ticket))))
            reply = msgspec.msgpack.decode(client.recv(), type=ReplyV1)
            self.assertEqual(reply.status, 'rejected' if raises else 'submission_failed')
            self.assertEqual(len(endpoint.handles), 1)
            self.assertEqual(self.transfers.states[endpoint.handles[0].sequence].phase, 'closed')
            self.assert_drained()
        finally:
            client.close()
            context.term()
            endpoint.close()

    def test_socket_pre_enqueue_validation_failure_releases_original_claim(self):
        self.socket_rejection(True)

    def test_socket_admission_closed_releases_only_unsubmitted_claim(self):
        self.socket_rejection(False)
