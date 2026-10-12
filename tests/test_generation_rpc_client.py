from concurrent.futures import Future
from dataclasses import replace
import pytest

from scripts.generation_rpc_client import GenerationRPCClient
from scripts.generation_session_ledger import GenerationSessionLedger
from scripts.owned_service_protocol import SEQUENCE_CONFIG, wire_id
from test_owned_service_protocol import Key


class Transport:
    def __init__(self):
        self.calls, self.futures = [], []

    def ping(self, value):
        self.calls.append(('ping', value))
        future = Future()
        future.set_result(True)
        return future

    def __getattr__(self, name):
        def send(key, *args, **kwargs):
            self.calls.append((name, getattr(key, 'request_id', key)))
            future = Future()
            self.futures.append(future)
            return future
        return send


def setup():
    transport = Transport()
    return transport, GenerationRPCClient(transport, 7), Key()


def test_lookup_ack_status_and_free_each_gate_exact_end():
    transport, client, key = setup()
    lookup = client.lookup(key)
    assert client.poll_rpc() == []
    with pytest.raises(RuntimeError, match='acknowledgement'):
        client.query_prefetch_status(key.request_id)
    lookup.set_result(None)
    assert len(client.poll_rpc()) == 1
    status = client.query_prefetch_status(key.request_id)
    free = client.free_lookup_locks(key)
    assert client.end_generation(wire_id(key)) == []
    status.set_result(0)
    assert len(client.poll_rpc()) == 1
    assert client.end_generation(wire_id(key)) == []
    free.set_result(None)
    assert len(client.poll_rpc()) == 1
    assert client.end_generation(wire_id(key)) == [wire_id(key)]
    assert transport.calls[-1] == ('end_session', wire_id(key))


def test_old_status_future_survives_new_lookup_route():
    transport, client, old = setup()
    client.lookup(old).set_result(None)
    client.poll_rpc()
    status = client.query_prefetch_status(old.request_id)
    new = replace(old, request_configs={**old.request_configs, SEQUENCE_CONFIG: 2})
    lookup = client.lookup(new)
    status.set_result(2)
    done, = client.poll_rpc()
    assert done.wire == wire_id(old)
    assert client.ledger.lookup_routes[old.request_id] == wire_id(new)
    assert not lookup.done()
    assert client.end_generation(wire_id(old)) == [wire_id(old)]
    assert client.end_generation(wire_id(new)) == []


def test_store_only_successor_keeps_old_lookup_route():
    _, client, old = setup()
    client.lookup(old).set_result(None)
    client.poll_rpc()
    new = replace(old, request_configs={**old.request_configs, SEQUENCE_CONFIG: 2})
    client.reserve_store(new)
    client.query_prefetch_status(old.request_id)
    assert client.ledger.lookup_routes[old.request_id] == wire_id(old)
    assert client.ledger.current[old.request_id] == wire_id(new)


@pytest.mark.parametrize('error', [TimeoutError('timeout'), OSError('lost reply')])
def test_failed_rpc_keeps_owner_and_never_retries(error):
    transport, client, key = setup()
    future = client.lookup(key)
    future.set_exception(error)
    assert client.poll_rpc() == []
    assert client.poll_rpc() == []
    assert client.end_generation(wire_id(key)) == []
    assert len(transport.calls) == 2  # ping and one lookup
    assert next(iter(client.pending_rpc.values())).error == repr(error)


def test_submission_exception_is_unresolved_not_terminal():
    transport, client, key = setup()
    def fail(*args):
        raise OSError('may have sent')
    transport.lookup = fail
    with pytest.raises(OSError):
        client.lookup(key)
    assert len(client.pending_rpc) == 1
    assert client.end_generation(wire_id(key)) == []


def test_manual_rpc_terminal_cannot_skip_ack_observation():
    _, client, key = setup()
    client.lookup(key)
    ticket, = client.pending_rpc
    with pytest.raises(RuntimeError, match='future observation'):
        client.terminal(ticket)


def test_messaging_future_query_and_result_are_observed_once():
    transport, client, key = setup()
    class Messaging:
        ready = False
        results = 0
        def query(self):
            return self.ready
        def result(self, timeout):
            assert timeout == 0 and self.ready
            self.results += 1
    future = Messaging()
    transport.lookup = lambda *args: future
    client.lookup(key)
    assert client.poll_rpc() == []
    future.ready = True
    assert len(client.poll_rpc()) == 1
    assert client.poll_rpc() == []
    assert future.results == 1


def test_operation_capacity_retains_old_tickets_and_rejects_before_rpc():
    transport = Transport()
    client = GenerationRPCClient(transport, 7,
        GenerationSessionLedger(operation_capacity=1))
    key = Key()
    client.lookup(key).set_result(None)
    client.poll_rpc()
    before = list(transport.calls)
    with pytest.raises(RuntimeError, match='capacity'):
        client.query_prefetch_status(key.request_id)
    assert transport.calls == before
    assert client.ledger.serial == 1


@pytest.mark.parametrize('limit', [0, -1, True, 1.5])
def test_invalid_operation_limit(limit):
    with pytest.raises(ValueError, match='operation capacity'):
        GenerationSessionLedger(operation_capacity=limit)
