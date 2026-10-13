import queue
from types import SimpleNamespace as NS
import pytest
from cachepilot.runtime.idle_pump import GenerationInputQueue, install_generation_idle_pump
from cachepilot.runtime.idle import IdleInputQueue, install_idle_core


class Queue:
    def __init__(self, value=None):
        self.value, self.calls = value, []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        if self.value is not None:
            return self.value
        raise queue.Empty

    def empty(self):
        return self.value is None


def core(pending=True, work=False, running=True):
    calls = []
    connector = NS(generation_needs_poll=lambda: pending,
        _flush_generation_ends=lambda: calls.append('poll'),
        _idle_finalize=True, _idle_grace_seconds=15,
        _lazy_offload_manager=NS(_policy=NS(_pending={})))
    return NS(scheduler=NS(connector=connector), is_running=lambda: running,
              has_work=lambda: work), calls


@pytest.mark.parametrize('work,running,expected', [(False, True, ['poll']),
    (True, True, []), (False, False, [])])
def test_empty_poll_only_on_quiescent_owner_thread(work, running, expected):
    c, calls = core(work=work, running=running)
    original = Queue()
    with pytest.raises(queue.Empty):
        GenerationInputQueue(original, c).get()
    assert calls == expected
    assert original.calls == [dict(block=True, timeout=0.1)]


def test_arrival_wins_and_explicit_timeout_preserved():
    c, calls = core()
    original = Queue('arrival')
    wrapped = GenerationInputQueue(original, c)
    assert wrapped.get() == 'arrival'
    assert wrapped.get(timeout=7) == 'arrival'
    assert calls == []
    assert original.calls[-1] == dict(block=True, timeout=7)


def test_original_idle_wrapper_not_nested():
    class Core:
        def _process_input_queue(self):
            return self.input_queue
    c, _ = core()
    real = Core()
    real.scheduler = c.scheduler
    original = Queue('arrival')
    real.input_queue = IdleInputQueue(original, real)
    install_idle_core(Core)
    install_generation_idle_pump(Core)
    wrapped = real._process_input_queue()
    assert isinstance(wrapped, GenerationInputQueue)
    assert wrapped.original is original
    assert real._process_input_queue() is wrapped
    assert wrapped.get() == 'arrival'
