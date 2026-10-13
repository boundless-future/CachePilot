"""Installed MessagingFuture contract; transport delivery remains controlled."""
import pytest

pytest.importorskip('lmcache')
from lmcache.v1.multiprocess.futures import MessagingFuture
from cachepilot.integration.lmcache.client import GenerationRPCClient
from cachepilot.integration.lmcache.protocol import wire_id
from cachepilot_test_support.rpc import Key
from cachepilot_test_support.rpc import Transport


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
