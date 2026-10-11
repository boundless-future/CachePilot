from copy import deepcopy

import pytest

from scripts.audit_idle_finalize import check_events


@pytest.fixture
def evidence():
    rows=[]
    def add(event, tick, **fields):
        rows.append(dict(event=event, request_id='r', monotonic_ns=tick, **fields))
    add('lookup_registered', 1)
    token=dict(lock_id=7,epoch=1,serial=1)
    add('reservation_acquired', 2, **token)
    transfer=dict(sequence=1,kind='retrieve')
    add('transfer_submitted', 3, read_pins=1, **transfer)
    add('terminal_seen', 4, **transfer)
    add('reservation_released', 5, disposition='RELEASED', **token)
    add('transfer_retired', 6, succeeded=True, **transfer)
    add('diagnostic_end_session_enter', 7)
    add('lookup_reclaimed', 8)
    add('owned_shutdown_drained', 9)
    drop=dict(wire_id='r',acquired_pins=0,submitted_stores=0,dropped=1,ranges=[[0,1024]])
    return rows,[drop]


def test_accepts_terminal_before_release(evidence):
    result=check_events(*evidence)
    assert result['reservations']==result['transfers']==result['jobs']==1


@pytest.mark.parametrize('fault', ['missing_terminal','early_release','duplicate_release',
                                  'wrong_epoch','missing_end','missing_reclaim','unresolved',
                                  'unknown_drop','pin_mismatch'])
def test_rejects_incomplete_or_misattributed_lifecycle(evidence,fault):
    rows,drops=deepcopy(evidence)
    if fault=='missing_terminal': rows=[r for r in rows if r['event']!='terminal_seen']
    elif fault=='early_release':
        next(r for r in rows if r['event']=='reservation_released')['monotonic_ns']=3.5
    elif fault=='duplicate_release':
        rows.append(deepcopy(next(r for r in rows if r['event']=='reservation_released')))
    elif fault=='wrong_epoch':
        next(r for r in rows if r['event']=='reservation_released')['epoch']=2
    elif fault=='missing_end': rows=[r for r in rows if r['event']!='diagnostic_end_session_enter']
    elif fault=='missing_reclaim': rows=[r for r in rows if r['event']!='lookup_reclaimed']
    elif fault=='unresolved': rows.append(dict(event='ownership_unresolved'))
    elif fault=='unknown_drop': drops[0]['wire_id']='new-generation'
    else: next(r for r in rows if r['event']=='transfer_submitted')['read_pins']=2
    with pytest.raises(AssertionError): check_events(rows,drops)
