"""Run existing real-manager pin/receipt contracts through the diagnostic copy."""
import pytest
pytest.importorskip('lmcache')
from cachepilot_test_support.pool import case
from test_bounded_protection import test_pin_precedes_allocator_and_original_receipt_releases_once as original_lifecycle, test_invalid_or_unsupported_candidate_is_never_pinned as original_rejection
from scripts.capacity_diagnostic_protection import DiagnosticBoundedProtection
from cachepilot_test_support.generation import combined
from test_generation_protection_bridge import test_original_receipt_settles_prefix_then_suffix as original_generation


def activate(case, monkeypatch):
    helper, scheduler, blocks, rows = case
    helper.__class__ = DiagnosticBoundedProtection
    monkeypatch.setenv('CACHEPILOT_CAPACITY_DIAGNOSTICS', '1')
    record = helper.record
    diagnostic = []
    helper.record = lambda event, **kw: (diagnostic.append(dict(event=event, **kw))
        if event.endswith('_diagnostic') else record(event, **kw))
    return diagnostic


def test_real_pin_and_original_receipt(case, monkeypatch):
    events = activate(case, monkeypatch)
    original_lifecycle(case)
    boundary = next(e for e in events if e.get('stage') == 'boundary')
    assert boundary['reserve'] == 50
    assert boundary['pending']


@pytest.mark.parametrize('mutation', ['hash', 'tokens', 'referenced', 'capacity', 'active_id', 'inflight', 'partial'])
def test_real_rejections(case, monkeypatch, mutation):
    events = activate(case, monkeypatch)
    original_rejection(case, mutation)
    assert events


@pytest.mark.parametrize('chunks', [1, 2])
@pytest.mark.parametrize('failed', [False, True])
def test_real_generation_prefix_suffix_receipt(combined, monkeypatch, chunks, failed):
    from scripts.capacity_diagnostic_connector import CapacityBoundedProtection
    combined[0].__class__ = CapacityBoundedProtection
    monkeypatch.setenv('CACHEPILOT_CAPACITY_DIAGNOSTICS', '1')
    original_generation(combined, chunks, failed)
