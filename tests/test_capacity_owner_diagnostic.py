from threading import RLock
from types import SimpleNamespace

import pytest

from scripts.diagnose_capacity_owner import observe_method, owner_snapshot


def test_observation_keeps_arguments_return_and_exception():
    calls, events = [], []
    value = object()
    failure = RuntimeError('original failure')

    class Target:
        def query(self, request_id, *, fail=False):
            calls.append((request_id, fail))
            if fail:
                raise failure
            return value

    service = SimpleNamespace(gate=RLock(), record=lambda *a, **kw: events.append((a, kw)))
    observe_method(Target, 'query', service, lambda request_id, **kw: request_id)
    target = Target()
    assert target.query('r') is value
    with pytest.raises(RuntimeError) as exc:
        target.query('s', fail=True)
    assert exc.value is failure
    assert calls == [('r', False), ('s', True)]
    assert [a[0] for a, _ in events] == ['diagnostic_query_enter',
        'diagnostic_query_return', 'diagnostic_query_enter', 'diagnostic_query_error']


def test_snapshot_does_not_consume_or_mutate_owner():
    owner = SimpleNamespace(request_id='r', abandoned=False, consumed=False,
        claimed=False, handle=object(), hit_chunks=0, reads=[object()], writes={}, error=None)
    service = SimpleNamespace(jobs={'r': owner})
    row, = owner_snapshot(service)
    assert row['reads'] == 1 and row['has_handle']
    row['consumed'] = True
    assert not owner.consumed and len(owner.reads) == 1
    assert service.jobs['r'] is owner
