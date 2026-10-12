import copy
import importlib
from pathlib import Path
import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    return importlib.import_module('run_generation_matrix')


def snapshot():
    return dict(generations=[dict(sealed=True, end_state='submitted', error=None,
        operations=[dict(state='terminal'), dict(state='dropped')])],
        pending_store=0, inflight_store=0, retrieves=0, pending_rpc=0, deferred_ends=[])


def test_closed_is_not_just_empty_counters(module):
    good = snapshot()
    assert module.generation_closed(good)
    mutations = [('end_state', 'open'), ('sealed', False), ('error', 'uncertain')]
    for key, value in mutations:
        bad = copy.deepcopy(good)
        bad['generations'][0][key] = value
        assert not module.generation_closed(bad)
    bad = copy.deepcopy(good)
    bad['generations'][0]['operations'][0]['state'] = 'active'
    assert not module.generation_closed(bad)
    for key in ('pending_store', 'inflight_store', 'retrieves', 'pending_rpc'):
        bad = copy.deepcopy(good)
        bad[key] = 1
        assert not module.generation_closed(bad)
    bad = copy.deepcopy(good)
    bad['deferred_ends'] = ['old']
    assert not module.generation_closed(bad)


def test_new_config_preserves_original_grace_and_separate_lookup_stack(module):
    base = dict(kv_connector_extra_config={})
    result = module.generation_config(base, dict(mode='zero', horizon=5.0, drain=1))
    assert base == dict(kv_connector_extra_config={})
    assert result['kv_connector'] == 'GenerationLedgerConnector'
    extra = result['kv_connector_extra_config']
    assert extra['cachepilot.generation_ledger'] is True
    assert extra['cachepilot.idle_finalize_grace_seconds'] == 15
    assert extra['cachepilot.protection.max_chunks'] == 0
    assert extra['lmcache.mp.lazy_offload_horizon_steps'] == 5.0
    with pytest.raises(ValueError):
        module.generation_config(base, dict(mode='one', horizon=5.0, drain=1))


def test_decode_intervention_keeps_prompts_and_arrival_fields(module):
    root = Path(__file__).resolve().parents[1]
    import json
    import tarfile
    with tarfile.open(root / 'docs/experiments/2026-10-11/unprotected-reference-evidence/'
            'unprotected-references-formal-20261011a.tar.gz') as archive:
        member = next(m for m in archive.getmembers()
            if m.name.endswith('/trace.json') and m.name.count('/') == 1)
        base = json.load(archive.extractfile(member))
    original = copy.deepcopy(base)
    trace = module.extend_decode(base)
    assert base == original
    assert len(trace['events']) == 68
    for before, after in zip(base['events'], trace['events']):
        assert after['max_tokens'] == 192
        assert {k: v for k, v in after.items() if k != 'max_tokens'} == {
            k: v for k, v in before.items() if k != 'max_tokens'}
    base['events'][0]['arrival_ms'] += 1
    with pytest.raises(ValueError):
        module.extend_decode(base)
