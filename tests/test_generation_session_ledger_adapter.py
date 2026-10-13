from dataclasses import dataclass, field
from concurrent.futures import Future
import pytest

from cachepilot.runtime.generation import LedgerRequestClient
from cachepilot.integration.lmcache.protocol import CLIENT_CONFIG, GENERATION_CONFIG, SEQUENCE_CONFIG, wire_id


@dataclass
class Key:
    request_id: str = 'same'
    cache_salt: str = 'salt'
    request_configs: dict = field(default_factory=lambda: {
        CLIENT_CONFIG: 7, SEQUENCE_CONFIG: 1, GENERATION_CONFIG: 'a' * 32})


class RecordingClient:
    def __init__(self):
        self.calls = []

    def lookup(self, key, *args, **kwargs):
        self.calls.append(('lookup', key.request_id))
        return Future()

    def store(self, key, *args, **kwargs):
        self.calls.append(('store', key.request_id))
        return Future()

    def end_session(self, wire):
        self.calls.append(('end_session', wire))


def test_tracker_rebuild_store_and_end_keep_exact_generations():
    raw = RecordingClient()
    client = LedgerRequestClient(raw)
    old = Key()
    old_wire = wire_id(old)
    client.lookup(old)
    # A resumed tracker can submit STORE without issuing a new lookup.
    new = Key(request_configs={CLIENT_CONFIG: 7, SEQUENCE_CONFIG: 2,
                               GENERATION_CONFIG: 'b' * 32})
    new_wire = wire_id(new)
    client.store(new)
    new_ticket = next(t for t in client.tickets.values() if t.wire == new_wire)
    client.terminal(new_ticket)
    old_lookup = next(t for t in client.tickets.values() if t.wire == old_wire)
    client.terminal(old_lookup)
    assert client.end_generation(old_wire) == [old_wire]
    assert client.end_generation(new_wire) == [new_wire]
    assert raw.calls == [('lookup', old_wire), ('store', new_wire),
                         ('end_session', old_wire), ('end_session', new_wire)]


def test_successor_end_waits_for_its_unresolved_store():
    raw = RecordingClient()
    client = LedgerRequestClient(raw)
    old, new = Key(), Key(request_configs={CLIENT_CONFIG: 7, SEQUENCE_CONFIG: 2,
                                           GENERATION_CONFIG: 'b' * 32})
    client.lookup(old)
    client.terminal(next(iter(client.tickets.values())))
    client.store(new)
    assert client.end_generation(wire_id(old)) == [wire_id(old)]
    assert client.end_generation(wire_id(new)) == []
    assert [kind for kind, _ in raw.calls] == ['lookup', 'store', 'end_session']


def test_reserved_old_store_submits_after_successor_with_original_identity():
    raw = RecordingClient()
    client = LedgerRequestClient(raw)
    old = Key()
    identity = wire_id(old)
    ticket = client.reserve_store(old)
    new = Key(request_configs={CLIENT_CONFIG: 7, SEQUENCE_CONFIG: 2,
                               GENERATION_CONFIG: 'b' * 32})
    client.lookup(new)
    old.request_configs[GENERATION_CONFIG] = 'c' * 32
    client.submit_store(ticket)
    assert raw.calls[-1] == ('store', identity)
    assert client.ledger.current['same'] == wire_id(new)
    assert client.end_generation(identity) == []
    client.terminal(ticket)
    assert client.end_generation(identity) == [identity]


def test_raw_end_cannot_bypass_ledger_via_delegate():
    raw = RecordingClient()
    client = LedgerRequestClient(raw)
    with pytest.raises(RuntimeError, match='Raw-ID'):
        client.end_session('same')
    assert raw.calls == []
