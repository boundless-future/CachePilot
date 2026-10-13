from types import SimpleNamespace as NS
from dataclasses import replace
import pytest
pytest.importorskip('lmcache')
from cachepilot_test_support.pool import case
from scripts.shared_prefix_sparse_probe import SharedPrefixSparseProbeConnector
from scripts.diagnostic_connector import DiagnosticConnector, token_key


def test_sparse_keeps_intervening_store_markers_and_captures_protection(case, monkeypatch):
    helper, _, _, _ = case
    op = helper.policy._pending['old'][0].store_metadata.op
    obj = object.__new__(SharedPrefixSparseProbeConnector)
    obj.worker_adapter = NS(lmcache_tokens_per_chunk=256,
        engine_group_infos=[NS(tokens_per_block=16)], _block_ids_per_group=lambda o:o.block_ids)
    obj._bound_handoffs = []
    obj._probe_refs = {}
    rows, captures = [], []
    obj._record = rows.append
    monkeypatch.setattr(DiagnosticConnector, '_snapshot',
        lambda self, phase, rid, partial, salt: captures.append((phase, partial.start, partial.end)))
    obj._snapshot('before_store', 'old', op, '')
    assert len(rows) == 2 and all(r['sparse_marker'] and not r['layers'] for r in rows)
    assert not captures
    obj._bound_handoffs = [dict(request_id='old', source='protection', start=0, end=512)]
    obj._snapshot('before_store', 'old', op, '')
    assert captures == [('before_store',0,256), ('before_store',256,512)]
    obj._bound_handoffs = []
    obj._probe_refs[(token_key(op.token_ids, '', 256), 'layer')] = object()
    obj._snapshot('after_retrieve', 'new', op, '')
    assert captures[-1] == ('after_retrieve',0,256) and len(captures)==3
    obj._snapshot('before_store', 'other', op, '')
    assert len(rows) == 4 and rows[-2]['token_prefix_sha256']==rows[0]['token_prefix_sha256']
