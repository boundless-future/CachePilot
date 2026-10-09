import pytest

pytest.importorskip("lmcache")
from scripts.protection_receipt_probe import ProbedResult


class Future:
    ready = False
    def query(self):
        return self.ready
    def result(self):
        assert self.ready
        return True


def test_delay_never_turns_unfinished_future_into_completion():
    now = [0]
    future, events = Future(), []
    wrapped = ProbedResult(future, "delayed", lambda event, **kw: events.append((event, kw)), lambda: now[0])
    now[0] = 6
    assert not wrapped.query() and not events
    future.ready = True
    assert wrapped.query() and wrapped.result() is True
    assert len(events) == 2


def test_actual_ready_is_held_until_deadline():
    now = [0]
    future, events = Future(), []
    future.ready = True
    wrapped = ProbedResult(future, "delayed", lambda event, **kw: events.append((event, kw)), lambda: now[0])
    assert not wrapped.query()
    with pytest.raises(RuntimeError):
        wrapped.result()
    now[0] = 5
    assert wrapped.query() and wrapped.result() is True
    assert [r[0] for r in events] == ["protected_actual_ready", "protected_result_delivered"]


def test_failed_receipt_diagnostic_requires_actual_success():
    future = Future()
    future.ready = True
    wrapped = ProbedResult(future, "failed-after-success", lambda *a, **kw: None)
    assert wrapped.query() and wrapped.result() is False
    future.result = lambda: False
    with pytest.raises(RuntimeError, match="requires actual STORE success"):
        wrapped.result()
