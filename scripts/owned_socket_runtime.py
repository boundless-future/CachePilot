"""Loopback ZMQ adapter for the versioned owned-transfer prototype.

The trusted caller supplies the original job, registered worker and native
consumer. This is not LMCache's existing RPC protocol or a liveness monitor.
Submission replies do not acknowledge GPU completion.
"""
from dataclasses import replace
import threading

import msgspec
import zmq
from owned_wire_protocol import QueryReplyV1, decode_retrieve


class QueryV1(msgspec.Struct, tag='query', frozen=True, forbid_unknown_fields=True):
    version: int
    server: str
    sequence: int
    request_id: str


class TransferV1(msgspec.Struct, tag='retrieve', frozen=True, forbid_unknown_fields=True):
    version: int
    metadata: bytes


class ReplyV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: int
    status: str
    data: bytes = b''


class OwnedSocketEndpoint:
    """One trusted worker connection; bounded stop preserves native leases."""
    def __init__(self, sessions, native, worker, original_job, key, stream, arena, consumer):
        self.sessions, self.native = sessions, native
        self.worker, self.job, self.key = worker, original_job, key
        self.stream, self.arena, self.consumer = stream, arena, consumer
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.setsockopt(zmq.LINGER, 0)
        port = self.socket.bind_to_random_port('tcp://127.0.0.1')
        self.address = f'tcp://127.0.0.1:{port}'
        self.stopping = threading.Event()
        self.handles = []
        self.errors = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _dispatch(self, payload):
        message = msgspec.msgpack.decode(payload, type=QueryV1 | TransferV1)
        if message.version != 1:
            raise ValueError('Unsupported protocol version')
        if self.worker not in self.sessions.live or not self.sessions.accepting:
            raise ValueError('Worker disconnected')
        if isinstance(message, QueryV1):
            if (message.server, message.sequence, message.request_id) != (
                    self.job.server, self.job.sequence, self.job.external_request_id):
                raise ValueError('Query generation mismatch')
            wire = self.sessions.query(self.job)
            if wire is None:
                return ReplyV1(1, 'pending')
            reply = msgspec.msgpack.decode(wire, type=QueryReplyV1)
            tickets = tuple(t for t in reply.tickets if t.original().worker == self.worker)
            return ReplyV1(1, 'ready', msgspec.msgpack.encode(QueryReplyV1(1, reply.hit_chunks, tickets)))
        request = decode_retrieve(message.metadata)
        if request.ticket.original().worker != self.worker:
            raise ValueError('Connection worker mismatch')
        handle = self.sessions.retrieve(message.metadata,
            replace(self.key, start=request.start, end=request.end))
        if handle is None:
            raise ValueError('Reader claim unavailable')
        self.handles.append(handle)
        try:
            accepted = self.native.submit(handle, self.stream, self.arena, self.consumer)
        except Exception:
            self.sessions.transfers.reject_unsubmitted(handle)
            raise
        if not accepted:
            # This operation succeeds only for a still-prepared plan. It cannot
            # retire submitted/unknown work, including partially enqueued DMA.
            self.sessions.transfers.reject_unsubmitted(handle)
        return ReplyV1(1, 'submitted' if accepted else 'submission_failed')

    def _serve(self):
        try:
            while not self.stopping.is_set():
                if not self.socket.poll(50, zmq.POLLIN):
                    continue
                payload = self.socket.recv()
                try:
                    reply = self._dispatch(payload)
                except Exception as exc:
                    self.errors.append(repr(exc))
                    reply = ReplyV1(1, 'rejected')
                self.socket.send(msgspec.msgpack.encode(reply))
        finally:
            self.socket.close()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Socket consumer still submitting; do not free its resources')
        self.context.term()
