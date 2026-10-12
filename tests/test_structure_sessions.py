import importlib
from pathlib import Path
import pytest


@pytest.fixture
def analyzer(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    return importlib.import_module('analyze_structure_sessions')


def inputs():
    trace = dict(events=[dict(event_id=i, session_id='a' if i < 2 else 'b',
        source_file='first' if i < 2 else 'second', turn=i if i < 2 else 0) for i in range(3)])
    rows = [dict(**{k: e[k] for k in ('event_id', 'session_id', 'turn')}, error=None,
        scheduled_ms=i * 100, finished_ms=i * 100 + 50, ttft_ms=10,
        ready_to_first_ms=20, ready_to_end_ms=50, client_queue_ms=10)
        for i, e in enumerate(trace['events'])]
    return trace, dict(results=rows)


def test_sessions_preserve_offered_span_and_all_sources(analyzer):
    trace, data = inputs()
    result = analyzer.session_rows(trace, data)
    assert set(result) == {'first', 'second'}
    assert result['first']['completion_ms'] == 150
    assert result['second']['completion_ms'] == 50


@pytest.mark.parametrize('mutation', ['duplicate', 'failed', 'wrong_session'])
def test_missing_failed_or_misassigned_request_rejected(analyzer, mutation):
    trace, data = inputs()
    if mutation == 'duplicate':
        data['results'][2] = data['results'][0]
    elif mutation == 'failed':
        data['results'][0]['error'] = 'timeout'
    else:
        data['results'][0]['session_id'] = 'b'
    with pytest.raises(ValueError):
        analyzer.session_rows(trace, data)


def test_archived_pilot_cannot_produce_formal_session_summary(analyzer):
    root = Path(__file__).resolve().parents[1]
    directory = root / 'docs/experiments/2026-10-12/real-structure-evidence/real-structure-unobserved-pilot-20261012a'
    with pytest.raises(ValueError, match='Complete unobserved twelve-cell'):
        analyzer.analyze(directory)
