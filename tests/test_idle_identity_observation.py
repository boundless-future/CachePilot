from types import SimpleNamespace as NS

import pytest

from scripts.idle_identity_observation import observe, snapshot
from scripts.owned_service_protocol import CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG, wire_id


def state(current):
    meta=NS(request_id='r',op=NS(start=0,end=1024),request_configs={
        CLIENT_CONFIG:7,SEQUENCE_CONFIG:1,GENERATION_CONFIG:'a'*32})
    mapping={} if current=='missing' else {'r':wire_id(meta) if current=='same' else 'new-generation'}
    manager=NS(_policy=NS(_pending={'r':[NS(store_metadata=meta)]}),
        _requests=NS(is_finished=lambda rid:True,has_in_flight=lambda rid:False))
    return NS(_lazy_offload_manager=manager,scheduler_adapter=NS(req_clients={'url':NS(identities=mapping)}))


@pytest.mark.parametrize('current,expected',[('missing','missing_client'),('different','different_generation'),('same','matches')])
def test_observer_distinguishes_maps_without_modifying_identity(current,expected):
    connector=state(current)
    before=dict(connector.scheduler_adapter.req_clients['url'].identities)
    assert snapshot(connector)[0]['classification']==expected
    assert connector.scheduler_adapter.req_clients['url'].identities==before


def test_original_exception_identity_and_success_are_preserved():
    connector=state('missing'); records=[]
    error=RuntimeError('original guard')
    def fail(c,s): raise error
    with pytest.raises(RuntimeError) as caught:
        observe(fail,records.append)(connector,object())
    assert caught.value is error and len(records)==1
    assert observe(lambda c,s:9,records.append)(connector,object())==9
    assert len(records)==1
    def broken_record(row): raise OSError('disk full')
    with pytest.raises(RuntimeError) as caught:
        observe(fail,broken_record)(connector,object())
    assert caught.value is error
