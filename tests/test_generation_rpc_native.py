"""Installed MessagingFuture contract; transport delivery remains controlled."""
import pytest

pytest.importorskip('lmcache')
from lmcache.v1.multiprocess.futures import MessagingFuture
from scripts.generation_rpc_client import GenerationRPCClient
from scripts.owned_service_protocol import wire_id
from test_owned_service_protocol import Key
from test_generation_rpc_client import Transport


@pytest.mark.parametrize('outcome', ['none', 'false', 'error'])
def test_real_messaging_future_completion_preserves_end_gate(outcome):
    transport = Transport()
    future = MessagingFuture()
    transport.lookup = lambda *args: future
    client = GenerationRPCClient(transport, 7)
    key = Key()
    assert client.lookup(key) is future
    assert client.poll_rpc() == []
    assert client.end_generation(wire_id(key)) == []
    if outcome == 'error':
        future.set_exception(OSError('controlled reply failure'))
    else:
        future.set_result(None if outcome == 'none' else False)
    completed = client.poll_rpc()
    if outcome == 'error':
        assert completed == []
        assert client.end_generation(wire_id(key)) == []
        assert next(iter(client.pending_rpc.values())).error
    else:
        assert len(completed) == 1
        assert client.end_generation(wire_id(key)) == [wire_id(key)]
    assert client.poll_rpc() == []
