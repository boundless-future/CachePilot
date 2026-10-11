from types import SimpleNamespace as NS
import pytest
from scripts.identity_timeline_observation import observe_call


def test_transparent_result_arguments_and_before_after_state():
    state = []
    result = object()
    def operation(value, *, count):
        state.extend([value] * count)
        return result
    target = NS(operation=operation)
    rows = []
    observe_call(target, 'operation', lambda event, **row: rows.append((event, row)),
                 lambda args, kwargs: dict(state=list(state), count=kwargs['count']))
    assert target.operation('r', count=2) is result
    assert rows == [('operation_before', dict(state=[], count=2)),
                    ('operation_after', dict(state=['r', 'r'], count=2))]


def test_same_exception_and_no_after_event():
    error = RuntimeError('original failure')
    def fail(value):
        raise error
    target = NS(fail=fail)
    rows = []
    observe_call(target, 'fail', lambda event, **row: rows.append((event, row)),
                 lambda args, kwargs: dict(request_id=args[0]))
    with pytest.raises(RuntimeError) as caught:
        target.fail('r')
    assert caught.value is error
    assert [r[0] for r in rows] == ['fail_before', 'fail_error']


def test_observation_failure_is_visible_and_does_not_submit_rpc():
    calls = []
    target = NS(submit=calls.append)
    def fail(*args, **kwargs):
        raise OSError('diagnostic disk failure')
    observe_call(target, 'submit', fail, lambda args, kwargs: {})
    with pytest.raises(OSError):
        target.submit('r')
    assert calls == []
