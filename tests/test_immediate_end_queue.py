from types import SimpleNamespace as NS
import pytest
from scripts.immediate_end_queue import ImmediateReceiptClient


def test_exact_wire_waits_for_native_finished_sending_and_duplicate_is_noop():
    calls = []
    client = ImmediateReceiptClient(NS(end_session=lambda wire: calls.append(wire)))
    client.identities['raw'] = 'original-wire'
    client.end_session('raw')
    client.end_session('raw')
    client.flush_finished({'other'})
    assert calls == [] and client.pending_ends == {'raw': 'original-wire'}
    client.flush_finished({'raw'})
    client.flush_finished({'raw'})
    assert calls == ['original-wire']
    assert client.identities == client.pending_ends == {}


def test_failed_submission_retains_exact_pending_identity():
    def fail(wire):
        raise TimeoutError('original transport')
    client = ImmediateReceiptClient(NS(end_session=fail))
    client.identities['raw'] = 'wire'
    client.end_session('raw')
    with pytest.raises(TimeoutError):
        client.flush_finished({'raw'})
    assert client.identities == client.pending_ends == {'raw': 'wire'}


def test_overlapping_reuse_rejected_before_lookup_or_end():
    client = ImmediateReceiptClient(NS())
    client.identities['raw'] = 'old'
    client.end_session('raw')
    with pytest.raises(RuntimeError, match='reused'):
        client.lookup(NS(request_id='raw'))
    client.identities['raw'] = 'new'
    with pytest.raises(RuntimeError, match='overlaps'):
        client.end_session('raw')
    assert client.pending_ends == {'raw': 'old'}
