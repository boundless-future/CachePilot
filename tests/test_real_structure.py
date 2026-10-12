import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_real_structure import audit
from scripts.prepare_real_structure import build

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/experiments/2026-10-12'


def test_archived_token_trace_preserves_source_structure():
    result = audit(EVIDENCE / 'real-structure-built', EVIDENCE / 'real-structure-source',
                   EVIDENCE / 'REAL_STRUCTURE_CATALOGUE.json')
    assert result['passed'] and result['requests'] == 60
    assert result['cross_session_max_prefix_tokens'] == 0
    assert len(result['transitions']) == 54
    manifest = json.loads((EVIDENCE / 'real-structure-built/manifest.json').read_text())
    trace = build(EVIDENCE / 'real-structure-source',
                  json.loads((EVIDENCE / 'REAL_STRUCTURE_CATALOGUE.json').read_text()),
                  manifest['vocabulary'])
    archived = json.loads((EVIDENCE / 'real-structure-built/trace.json').read_text())
    assert trace == archived


def test_source_mutation_cannot_silently_change_workload(tmp_path):
    source = EVIDENCE / 'real-structure-source'
    for path in source.glob('*.json'):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    changed = tmp_path / 'trace_0021.json'
    changed.write_bytes(changed.read_bytes() + b' ')
    manifest = json.loads((EVIDENCE / 'real-structure-built/manifest.json').read_text())
    with pytest.raises(ValueError, match='Source bytes'):
        build(tmp_path, json.loads((EVIDENCE / 'REAL_STRUCTURE_CATALOGUE.json').read_text()),
              manifest['vocabulary'])


def test_trace_time_mutation_rejected_even_with_updated_digest(tmp_path):
    directory = EVIDENCE / 'real-structure-built'
    manifest = json.loads((directory / 'manifest.json').read_text())
    trace = json.loads((directory / 'trace.json').read_text())
    trace['events'][-1]['arrival_ms'] += 100
    manifest['trace_sha256'] = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
    (tmp_path / 'trace.json').write_text(json.dumps(trace))
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(AssertionError):
        audit(tmp_path, EVIDENCE / 'real-structure-source', EVIDENCE / 'REAL_STRUCTURE_CATALOGUE.json')
