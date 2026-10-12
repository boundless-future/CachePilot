"""Scheduler-thread RPC tracking candidate; not installed by a connector.

Only lookup/status/free use RPC completion as a terminal. STORE/RETRIEVE need
the existing trusted transfer receipt path, which this adapter does not install.
"""
from dataclasses import dataclass, replace

from scripts.generation_session_ledger import LedgerRequestClient
from scripts.owned_service_protocol import wire_id


@dataclass
class PendingRPC:
    future: object = None
    error: str | None = None


class GenerationRPCClient(LedgerRequestClient):
    def __init__(self, client, incarnation, ledger=None):
        super().__init__(client, ledger)
        self.incarnation = incarnation
        self.registered = False
        self.pending_rpc = {}

    def ping(self, instance_id=None):
        return self.client.ping(-self.incarnation if instance_id is None else instance_id)

    def _rpc(self, wire, kind, send):
        ticket = self.ledger.begin(wire, kind)
        self.tickets[ticket.serial] = ticket
        pending = self.pending_rpc[ticket] = PendingRPC()
        try:
            pending.future = send()
            if pending.future is None:
                raise RuntimeError('Tracked RPC did not return a completion future')
        except Exception as exc:
            # Submission might have reached the server. No retry or terminal.
            pending.error = repr(exc)
            raise
        return pending.future

    def lookup(self, key, *args, **kwargs):
        if not self.registered:
            if not self.ping().result(timeout=10):
                raise RuntimeError('Owned client incarnation rejected')
            self.registered = True
        wire = self._register(key)
        return self._rpc(wire, 'lookup', lambda: self.client.lookup(
            replace(key, request_id=wire), *args, **kwargs))

    def _lookup_route(self, request_id):
        wire = self.ledger.lookup_routes[request_id]
        generation = self.ledger.generations[wire]
        if any(t.kind == 'lookup' and state != 'terminal'
               for t, state in generation.operations.items()):
            raise RuntimeError('Status cannot overtake lookup acknowledgement')
        return wire

    def query_prefetch_status(self, request_id):
        wire = self._lookup_route(request_id)
        return self._rpc(wire, 'status', lambda: self.client.query_prefetch_status(wire))

    def query_prefetch_lookup_hits(self, request_id):
        wire = self._lookup_route(request_id)
        return self._rpc(wire, 'status', lambda: self.client.query_prefetch_lookup_hits(wire))

    def free_lookup_locks(self, key, *args, **kwargs):
        # Cleanup keys retain acquisition identity even after raw-ID reuse.
        wire = wire_id(key)
        return self._rpc(wire, 'free', lambda: self.client.free_lookup_locks(
            replace(key, request_id=wire), *args, **kwargs))

    def terminal(self, ticket):
        if ticket in self.pending_rpc:
            raise RuntimeError('RPC terminal requires successful future observation')
        return super().terminal(ticket)

    def poll_rpc(self):
        """Nonblocking observation on the owner thread, never an RPC callback.

        Pinned MessagingFuture query()/result() and concurrent Future done()
        are supported. Exceptions retain active ownership without auto retry.
        A successful result of None/False is still RPC completion, not a miss
        or transfer success claim. Server ownership audits remain necessary.
        """
        completed = []
        for ticket, pending in list(self.pending_rpc.items()):
            if pending.error is not None:
                continue
            try:
                future = pending.future
                ready = future.query() if hasattr(future, 'query') else future.done()
                if not ready:
                    continue
                future.result(timeout=0)
            except Exception as exc:
                pending.error = repr(exc)
                continue
            self.ledger.terminal(ticket)
            del self.pending_rpc[ticket]
            completed.append(ticket)
        return completed
