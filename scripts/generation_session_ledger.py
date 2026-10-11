"""Experimental scheduler-owned session ledger; not installed by any connector.

Tickets capture acquisition identity. A scheduler barrier must seal a generation
only after every possible producer has registered its work. END submission is
not acknowledgement and cannot establish server resource reclamation.
"""
from dataclasses import dataclass, field, replace

from scripts.owned_service_protocol import parse_id, wire_id


@dataclass(frozen=True)
class Ticket:
    wire: str
    serial: int
    kind: str


@dataclass
class Generation:
    raw: str
    wire: str
    client: int
    sequence: int
    sealed: bool = False
    operations: dict = field(default_factory=dict)
    end_state: str = 'open'
    error: str | None = None


class GenerationSessionLedger:
    """Single scheduler thread contract, with bounded lifetime tombstones.

No RPC callbacks may mutate this ledger directly. They must deliver the original
ticket to the scheduler. Unknown/failed completion retains ownership. Tombstones
are never evicted: a fresh client incarnation is required at the lifetime limit.
"""
    def __init__(self, capacity=4096):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError('Positive ledger capacity required')
        self.capacity = capacity
        self.generations = {}
        self.current = {}
        self.lookup_routes = {}
        self.sequence_owners = {}
        self.high = {}
        self.serial = 0

    def register(self, raw, wire):
        client, sequence = parse_id(wire)
        if wire.split(':', 4)[4] != raw:
            raise ValueError('Wire/raw identity mismatch')
        if wire in self.generations:
            if self.current.get(raw) != wire or self.generations[wire].sealed:
                raise RuntimeError('Retired generation cannot become current')
            return
        if len(self.generations) >= self.capacity:
            raise RuntimeError('Ledger lifetime capacity exhausted')
        if (client, sequence) in self.sequence_owners:
            raise RuntimeError('Sequence identity collision')
        previous = self.current.get(raw)
        if previous is not None:
            old = self.generations[previous]
            if old.client != client or sequence <= old.sequence:
                raise RuntimeError('Successor must advance the same incarnation')
        self.generations[wire] = Generation(raw, wire, client, sequence)
        self.sequence_owners[client, sequence] = wire
        self.current[raw] = wire

    def begin(self, wire, kind):
        if kind not in ('pending_store', 'lookup', 'status', 'free', 'retrieve'):
            raise ValueError('Unsupported operation kind')
        generation = self.generations[wire]
        if generation.sealed or generation.end_state != 'open':
            raise RuntimeError('Generation is sealed')
        if kind == 'lookup':
            if self.current.get(generation.raw) != wire:
                raise RuntimeError('Lookup requires current generation')
            if generation.sequence <= self.high.get(generation.client, 0):
                raise RuntimeError('Lookup would cross END high water')
            if any(t.kind == 'lookup' for t in generation.operations):
                raise RuntimeError('Duplicate lookup')
        self.serial += 1
        ticket = Ticket(wire, self.serial, kind)
        generation.operations[ticket] = 'pending' if kind == 'pending_store' else 'active'
        if kind == 'lookup':
            self.lookup_routes[generation.raw] = wire
        return ticket

    def submit_store(self, ticket):
        generation = self.generations[ticket.wire]
        if (ticket.kind != 'pending_store'
                or generation.operations.get(ticket) != 'pending'
                or generation.end_state != 'open'):
            raise RuntimeError('Unknown or non-pending STORE ticket')
        # May happen after successor registration or producer seal. The pending
        # ticket already reserved this exact generation before either event.
        generation.operations[ticket] = 'active'

    def terminal(self, ticket):
        generation = self.generations[ticket.wire]
        if generation.operations.get(ticket) != 'active':
            raise RuntimeError('Unknown, duplicate or non-active terminal')
        generation.operations[ticket] = 'terminal'

    def drop_pending(self, ticket):
        generation = self.generations[ticket.wire]
        if generation.operations.get(ticket) != 'pending':
            raise RuntimeError('Cannot drop submitted work')
        generation.operations[ticket] = 'dropped'

    def seal(self, wire):
        # This is an explicit producer barrier, NOT inferred from raw-id finish,
        # preemption, cancellation, a new tracker or transport method return.
        self.generations[wire].sealed = True

    def ready(self, wire):
        generation = self.generations[wire]
        if (not generation.sealed or generation.end_state != 'open'
                or any(s not in ('terminal', 'dropped')
                       for s in generation.operations.values())):
            return False
        # END affects admission across all raw IDs belonging to the client.
        # An older registered producer may still issue lookup; a submitted
        # lookup must reach its real acknowledgement before END may pass it.
        for other in self.generations.values():
            if other.client != generation.client or other.sequence > generation.sequence:
                continue
            if other.wire != wire and not other.sealed:
                return False
            if any(t.kind == 'lookup' and state != 'terminal'
                   for t, state in other.operations.items()):
                return False
        return True

    def submit_ends(self, send):
        """Submit eligible exact identities once. Failures remain unresolved.

        The caller must arrange server observation separately. A None return,
        future or exception can never be interpreted as successful reclamation.
        Independent eligible generations continue after one submission fails.
        """
        submitted = []
        for generation in sorted(self.generations.values(), key=lambda g: (g.client, g.sequence)):
            if not self.ready(generation.wire):
                continue
            generation.end_state = 'submitting'
            # The call may send before raising; conservatively retain the fence.
            self.high[generation.client] = max(self.high.get(generation.client, 0),
                                               generation.sequence)
            try:
                send(generation.wire)
            except Exception as exc:
                generation.end_state = 'unresolved'
                generation.error = repr(exc)
            else:
                generation.end_state = 'submitted'
                submitted.append(generation.wire)
        return submitted


class LedgerRequestClient:
    """Opt-in request-client boundary backed by the ledger.

    This adapter is deliberately small: completion callbacks must call
    ``terminal(ticket)`` on the scheduler thread. It never turns a transport
    return into a terminal event and it has no raw-ID END fallback.
    """
    def __init__(self, client, ledger=None):
        self.client = client
        self.ledger = ledger or GenerationSessionLedger()
        self.tickets = {}
        self.store_keys = {}

    def end_session(self, request_id):
        raise RuntimeError('Raw-ID END is unsupported; use exact generation')

    def _wire(self, key):
        return wire_id(key)

    def _register(self, key):
        wire = self._wire(key)
        self.ledger.register(key.request_id, wire)
        return wire

    def lookup(self, key, *args, **kwargs):
        wire = self._register(key)
        ticket = self.ledger.begin(wire, 'lookup')
        self.tickets[ticket.serial] = ticket
        # A failed submission retains its unresolved ticket.
        return self.client.lookup(replace(key, request_id=wire), *args, **kwargs)

    def reserve_store(self, key):
        wire = self._register(key)
        ticket = self.ledger.begin(wire, 'pending_store')
        self.tickets[ticket.serial] = ticket
        self.store_keys[ticket] = replace(key, request_id=wire,
                                         request_configs=dict(key.request_configs))
        return ticket

    def submit_store(self, ticket, *args, **kwargs):
        key = self.store_keys[ticket]
        self.ledger.submit_store(ticket)
        return self.client.store(key, *args, **kwargs)

    def store(self, key, *args, **kwargs):
        return self.submit_store(self.reserve_store(key), *args, **kwargs)

    def terminal(self, ticket):
        self.ledger.terminal(ticket)

    def end_generation(self, wire):
        self.ledger.seal(wire)
        return self.ledger.submit_ends(self.client.end_session)
