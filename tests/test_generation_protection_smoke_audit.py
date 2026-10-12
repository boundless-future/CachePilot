"""Reject tampered evidence from the archived real GPU normal-path run."""
import json
from pathlib import Path
import shutil
import pytest
from scripts.audit_generation_protection_smoke import audit_cell


SOURCE = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12/generation-protection-gpu/generation-protection-normal-20261012b/one'


@pytest.mark.parametrize('change', ['none', 'poison', 'pending', 'missing_observer', 'unequal_kv'])
def test_real_evidence_requires_all_gates(tmp_path, change):
    shutil.copytree(SOURCE, tmp_path / 'cell')
    cell = tmp_path / 'cell'
    if change in ('poison', 'pending'):
        path = cell / 'generation.json'
        data = json.loads(path.read_text())
        data['protection_poisoned' if change == 'poison' else 'pending_store'] = 'fault' if change == 'poison' else 1
        path.write_text(json.dumps(data))
    elif change == 'missing_observer':
        path = cell / 'result.json'
        data = json.loads(path.read_text())
        del data['running_final']['generation_observer']
        path.write_text(json.dumps(data))
    elif change == 'unequal_kv':
        path = next((cell / 'probe').glob('kv-*.jsonl'))
        data = [json.loads(line) for line in path.read_text().splitlines()]
        loaded = next(r for r in data if r['phase'] == 'after_retrieve')
        loaded['layers'][0]['bitwise_equal'] = False
        path.write_text('\n'.join(json.dumps(r) for r in data)+'\n')
    if change in ('poison', 'pending', 'unequal_kv'):
        with pytest.raises(AssertionError):
            audit_cell(cell)
    else:
        assert audit_cell(cell)['passed'] == (change == 'none')
