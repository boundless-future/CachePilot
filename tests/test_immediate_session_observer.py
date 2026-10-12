import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts.immediate_session_observer import install_creation_observer


def test_observer_preserves_session_identity_and_original_exception(monkeypatch):
    module = ModuleType('lmcache.v1.multiprocess.session')

    class Manager:
        def __init__(self):
            self.sessions = {}

        def get_or_create(self, request_id):
            if request_id == 'error':
                raise ValueError('original error')
            return self.sessions.setdefault(request_id, object())

    module.SessionManager = Manager
    monkeypatch.setitem(sys.modules, module.__name__, module)
    events = []
    service = SimpleNamespace(record=lambda *args, **kw: events.append((args, kw)))
    install_creation_observer(service)
    manager = Manager()
    first = manager.get_or_create('wire')
    assert manager.get_or_create('wire') is first
    manager.sessions.pop('wire')
    assert manager.get_or_create('wire') is not first
    with pytest.raises(ValueError, match='original error'):
        manager.get_or_create('error')
    assert len(events) == 3
    assert events[0][1]['session_object'] == events[1][1]['session_object'] == id(first)
    assert events[2][1]['session_object'] != id(first)
    assert events[0][0] == ('diagnostic_session_access', 'wire')
