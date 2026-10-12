from copy import deepcopy
import pytest
from scripts.audit_generation_pilot import audit_rows


def evidence():
    handoffs = [dict(event='generation_registered', wire_id=w, role='SCHEDULER')
                for w in ('A', 'B')]
    handoffs += [dict(event='generation_end_submitted', wire_id=w, role='SCHEDULER')
                 for w in ('A', 'B')]
    handoffs += [dict(event=e, batch_id='B:ledger:1',
        role='SCHEDULER' if e.startswith('scheduler') else 'WORKER')
        for e in ('scheduler_built', 'worker_bound', 'worker_submit_returned',
                  'worker_receipt', 'scheduler_receipt')]
    handoffs.append(dict(event='generation_before_shutdown', role='SCHEDULER',
        generations=[dict(sealed=True, end_state='submitted', operations=[
            dict(serial=1, kind='lookup', state='terminal')])],
        pending_store=0, inflight_store=0, retrieves=0, deferred_ends=[], pending_rpc=0))
    service = [dict(event='generation_server_end_returned', request_id=w,
                   session_remains=False) for w in ('A', 'B')]
    service += [dict(event='lookup_registered', request_id='A'),
                dict(event='transfer_submitted', request_id='B')]
    empty = dict(generation_observer=dict(session_ids=[], owned_job_ids=[], transfer_wire_ids=[]))
    return handoffs, service, dict(status_before_engine_stop=deepcopy(empty),
                                 status_after_engine_stop=deepcopy(empty))


def test_store_only_generation_requires_its_own_server_end():
    h, s, r = evidence()
    result = audit_rows(h, s, r)
    assert result['passed'] and result['store_only_wires'] == ['B']


@pytest.mark.parametrize('change', ['missing_server_end', 'duplicate_end',
    'session_remains', 'active_rpc', 'missing_snapshot', 'wire_unregistered',
    'wrong_receipt', 'lookup_late'])
def test_reject_incomplete_or_wrong_generation_evidence(change):
    h, s, r = evidence()
    if change == 'missing_server_end':
        s.pop(1)
    elif change == 'duplicate_end':
        s.append(dict(s[0]))
    elif change == 'session_remains':
        s[0]['session_remains'] = True
    elif change == 'active_rpc':
        h[-1]['generations'][0]['operations'][0]['state'] = 'active'
    elif change == 'missing_snapshot':
        del r['status_before_engine_stop']['generation_observer']
    elif change == 'wire_unregistered':
        s[-1]['request_id'] = 'unknown'
    elif change == 'wrong_receipt':
        h[-2]['batch_id'] = 'other'
    else:
        s.append(dict(event='lookup_rejected_late', request_id='A'))
    assert not audit_rows(h, s, r)['passed']
