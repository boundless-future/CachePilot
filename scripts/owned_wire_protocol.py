"""Versioned experimental QUERY/RETRIEVE messages; no legacy-wire fallback.

The dispatcher decodes untrusted metadata before any claim. ReaderTicket is
reconstructed from the original server reply, never from a key or bitmap.
"""
from typing import Literal

import msgspec
from owned_lookup_contract import ReaderTicket, Worker
from owned_prefetch_contract import JobHandle


class TicketV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    server: str
    sequence: int
    request_id: str
    worker: str
    rank: int

    @classmethod
    def capture(cls, ticket):
        return cls(ticket.lookup.server, ticket.lookup.sequence,
                   ticket.lookup.external_request_id, ticket.worker.incarnation,
                   ticket.worker.rank)

    def original(self):
        if not self.server or not self.worker or self.sequence < 0 or self.rank < 0:
            raise ValueError("Invalid original ticket")
        return ReaderTicket(JobHandle(self.server, self.sequence, self.request_id),
                            Worker(self.worker, self.rank))


class QueryReplyV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: Literal[1]
    hit_chunks: int
    tickets: tuple[TicketV1, ...]


class RetrieveV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: Literal[1]
    ticket: TicketV1
    registration: str
    block_ids: tuple[tuple[int, ...], ...]
    start: int
    end: int
    skip: int = 0


def decode_retrieve(payload):
    request = msgspec.msgpack.decode(payload, type=RetrieveV1)
    request.ticket.original()
    if (not request.registration or request.start < 0 or request.end <= request.start
            or request.skip < 0 or not request.block_ids
            or any(not ids or any(i < 0 for i in ids) for ids in request.block_ids)):
        raise ValueError("Invalid transfer envelope")
    return request


class OwnedSessionRuntime:
    """Trusted liveness notification gates late requests and abandons jobs.

    Disconnect is an explicit server observation, not a client-controlled
    timeout. Pending I/O and DMA are retained until their own terminal proof.
    """
    def __init__(self, lookup, transfers):
        self.lookup = lookup
        self.transfers = transfers
        self.live = set()
        self.retired = set()
        self.accepting = True

    def register(self, worker):
        with self.lookup.gate:
            if not self.accepting or worker in self.live or worker in self.retired:
                raise ValueError("Worker incarnation cannot be reused")
            self.live.add(worker)

    def begin(self, key, workers):
        with self.lookup.gate:
            if not self.accepting or not set(workers).issubset(self.live):
                raise ValueError("Late LOOKUP from disconnected worker")
            return self.lookup.begin(key, workers)

    def query(self, handle):
        with self.lookup.gate:
            result = self.lookup.query_owned(handle)
            if result is None:
                return None
            return msgspec.msgpack.encode(QueryReplyV1(
                1, result.hit_chunks, tuple(TicketV1.capture(t) for t in result.tickets)))

    def retrieve(self, payload, key):
        request = decode_retrieve(payload)
        ticket = request.ticket.original()
        with self.transfers.gate, self.lookup.gate:
            if not self.accepting or ticket.worker not in self.live:
                raise ValueError("RETRIEVE from disconnected worker")
            if key.start != request.start or key.end != request.end:
                raise ValueError("Wire range disagrees with captured key")
            return self.transfers.prepare_plan(ticket, key, request.block_ids,
                registration=request.registration, skip_first_n_tokens=request.skip)

    def disconnect(self, worker):
        with self.lookup.gate:
            self.live.discard(worker)
            self.retired.add(worker)
            for job in tuple(self.lookup.jobs.values()):
                if worker in job.workers:
                    self.lookup.end_owned(job.handle)

    def shutdown(self):
        with self.lookup.gate:
            self.accepting = False
            for worker in tuple(self.live):
                self.disconnect(worker)
            return {"drained": not self.lookup.jobs and not self.lookup.l1.leases,
                    "jobs": len(self.lookup.jobs), "leases": len(self.lookup.l1.leases)}
