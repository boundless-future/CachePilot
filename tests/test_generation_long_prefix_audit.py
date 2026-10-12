import json
from pathlib import Path
import shutil
import pytest
from scripts.audit_generation_long_prefix import audit

SOURCE = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12/generation-protection-long-prefix/generation-protection-long-prefix-20261012a/two'


@pytest.mark.parametrize('change', ['none', 'missing_lineage', 'self_parent', 'pending_child'])
def test_real_split_requires_terminal_derived_ticket(tmp_path, change):
    cell = tmp_path / 'cell'
    shutil.copytree(SOURCE, cell)
    path = cell / 'generation.json'
    snapshot = json.loads(path.read_text())
    if change == 'missing_lineage':
        snapshot['derived_tickets'] = []
    elif change == 'self_parent':
        snapshot['derived_tickets'][0]['parent_serial'] = snapshot['derived_tickets'][0]['serial']
    elif change == 'pending_child':
        link = snapshot['derived_tickets'][0]
        for g in snapshot['generations']:
            for op in g['operations']:
                if op['serial'] == link['serial']:
                    op['state'] = 'pending'
    path.write_text(json.dumps(snapshot))
    if change == 'none':
        assert audit(cell)['passed']
    else:
        with pytest.raises(AssertionError):
            audit(cell)
