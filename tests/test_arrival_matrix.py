import copy
import json
from pathlib import Path

import pytest

from scripts import run_arrival_matrix as arrival


def test_compression_preserves_every_non_arrival_event_field(monkeypatch):
    base = dict(trace_id="sample", events=[dict(event_id=0, arrival_ms=1, prompt="A", max_tokens=48),
        dict(event_id=1, arrival_ms=1000, prompt="AB", max_tokens=48)])
    original = copy.deepcopy(base)
    monkeypatch.setattr(arrival, "BASE_TRACE_SHA", arrival.digest(base))
    result = arrival.compress_arrivals(base)
    assert base == original
    assert [e["arrival_ms"] for e in result["events"]] == [0.5, 500]
    for expected, actual in zip(base["events"], result["events"]):
        assert {k: v for k, v in expected.items() if k != "arrival_ms"} == {
            k: v for k, v in actual.items() if k != "arrival_ms"}


def test_unknown_trace_is_rejected():
    with pytest.raises(ValueError, match="preregistered"):
        arrival.compress_arrivals(dict(trace_id="unknown", events=[]))


def test_pilot_requires_independent_audit_not_only_pass_flags(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import summarize_reference_matrix
    signature, cases = {"source": "frozen"}, {"native": {"kind": "native"}}
    plan = dict(stage="pilot", repeats=1, signature=signature, cases=cases)
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(summarize_reference_matrix, "collect", lambda path: {"matrix_complete": False})
    with pytest.raises(ValueError, match="independent audit"):
        arrival.validate_pilot(tmp_path, signature, cases)
    monkeypatch.setattr(summarize_reference_matrix, "collect", lambda path: {"matrix_complete": True})
    arrival.validate_pilot(tmp_path, signature, cases)
    with pytest.raises(ValueError, match="signature differs"):
        arrival.validate_pilot(tmp_path, {"source": "changed"}, cases)
