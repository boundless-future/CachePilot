import pytest
from scripts.audit_generation_ownership import audit_records


def records():
    token = dict(request_id='A', lock_id=1, epoch=1, serial=1)
    transfer = dict(request_id='A', sequence=7, kind='retrieve')
    service = [dict(event='lookup_registered', request_id='A'),
        dict(event='reservation_acquired', monotonic_ns=1, **token),
        dict(event='transfer_submitted', monotonic_ns=2, read_pins=1, **transfer),
        dict(event='terminal_seen', monotonic_ns=3, **transfer),
        dict(event='reservation_released', monotonic_ns=4, disposition='RELEASED', **token),
        dict(event='transfer_retired', monotonic_ns=5, **transfer),
        dict(event='lookup_reclaimed', request_id='A'), dict(event='owned_shutdown_drained')]
    pool = [dict(free_blocks=909, registered_ids=[], tracked_refs={}, deferred_frees=0)] * 2
    return service, pool


def test_native_identity_and_pool():
    assert audit_records(*records())['passed']


@pytest.mark.parametrize('change', ['early_release', 'wrong_token', 'wrong_owner',
    'missing_terminal', 'duplicate_release', 'missing_pool', 'expired'])
def test_bad_evidence_rejected(change):
    service, pool = records()
    if change == 'early_release':
        service[4]['monotonic_ns'] = 2.5
    elif change == 'wrong_token':
        service[4]['serial'] = 2
    elif change == 'wrong_owner':
        service[4]['request_id'] = 'B'
    elif change == 'missing_terminal':
        service.pop(3)
    elif change == 'duplicate_release':
        service.append(dict(service[4]))
    elif change == 'missing_pool':
        pool = []
    else:
        service.append(dict(event='client_expired'))
    with pytest.raises(AssertionError):
        audit_records(service, pool)
