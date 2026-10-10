import pytest

pytest.importorskip("lmcache")
from scripts.protection_shared_cancel_probe import GatedResult


class Future:
    ready = False

    def query(self):
        return self.ready

    def result(self):
        assert self.ready
        return True


def test_real_completion_and_gate_both_required(tmp_path):
    future, events = Future(), []
    gate = tmp_path / "gate"
    result = GatedResult(future, gate, lambda event, **kw: events.append(event))
    assert not result.query()
    future.ready = True
    assert not result.query()
    with pytest.raises(RuntimeError):
        result.result()
    gate.touch()
    assert result.query() and result.query()
    assert result.result() is True
    assert events == ["shared_actual_ready", "shared_gate_opened", "shared_result_delivered"]


def test_gate_alone_never_completes_real_transfer(tmp_path):
    gate = tmp_path / "gate"
    gate.touch()
    result = GatedResult(Future(), gate, lambda *a, **kw: None)
    assert not result.query()
    with pytest.raises(RuntimeError):
        result.result()


def test_timeout_never_fabricates_receipt(tmp_path):
    now = [0]
    future = Future()
    future.ready = True
    result = GatedResult(future, tmp_path / "gate", lambda *a, **kw: None, lambda: now[0], timeout=1)
    now[0] = 1
    with pytest.raises(TimeoutError):
        result.query()
    with pytest.raises(RuntimeError):
        result.result()


def test_false_real_store_not_reported_as_success(tmp_path):
    gate = tmp_path / "gate"
    gate.touch()
    future = Future()
    future.ready = True
    future.result = lambda: False
    result = GatedResult(future, gate, lambda *a, **kw: None)
    assert result.query()
    with pytest.raises(RuntimeError, match="actual STORE success"):
        result.result()
