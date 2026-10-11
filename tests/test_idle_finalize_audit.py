from copy import deepcopy

import pytest

from scripts.audit_idle_finalize import check_events
from scripts.audit_idle_finalize import check_raw_id_reuse


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


@pytest.fixture
def reuse():
    first={0:dict(api_request_id='same',response_id='cmpl-same')}
    repeat=deepcopy(first)
    old,new='cp1:1:1:old:cmpl-same-0','cp1:1:2:new:cmpl-same-0'
    rows=[dict(event='lookup_registered',request_id=old,monotonic_ns=1),
          dict(event='lookup_reclaimed',request_id=old,monotonic_ns=2),
          dict(event='lookup_registered',request_id=new,monotonic_ns=3)]
    return first,repeat,rows,[dict(wire_id=old)]


def test_raw_id_proof_pairs_reclaimed_generation(reuse):
    result=check_raw_id_reuse(*reuse)
    assert result['raw_ids']==1
    assert result['dropped_generation_reentry']==['cmpl-same-0']


@pytest.mark.parametrize('fault',['no_drop','late_reclaim','same_generation','different_api','missing_lookup'])
def test_raw_id_proof_rejects_weak_or_missing_evidence(reuse,fault):
    first,repeat,rows,drops=reuse
    if fault=='no_drop': drops.clear()
    elif fault=='late_reclaim': rows[1]['monotonic_ns']=4
    elif fault=='same_generation': rows[2]['request_id']=rows[0]['request_id']
    elif fault=='different_api': repeat[0]['api_request_id']='new'
    else: rows.pop()
    with pytest.raises(AssertionError): check_raw_id_reuse(first,repeat,rows,drops)
