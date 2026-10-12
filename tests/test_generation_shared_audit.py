import json
from pathlib import Path
import shutil
import pytest
from scripts.audit_generation_shared import audit

SOURCE = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12/generation-shared-evidence/generation-shared-cancel-20261012a/two'


@pytest.mark.parametrize('change', ['none', 'early_free', 'missing_end'])
def test_joint_audit_requires_protection_and_generation(tmp_path, change):
    cell = tmp_path / 'cell'
    shutil.copytree(SOURCE, cell)
    if change != 'none':
        p = next((cell / 'events').glob('handoff-*-SCHEDULER.jsonl'))
        rows = [json.loads(line) for line in p.read_text().splitlines()]
        target = next(r for r in rows if r['event'] == 'shared_target')
        if change == 'early_free':
            row = next(r for r in rows if r['event'] == 'shared_free_after')
            row['refs'] = {k: 0 for k in row['refs']}
        else:
            rows = [r for r in rows if not (r['event'] == 'generation_end_submitted' and r['wire_id'] == target['wire_id'])]
        p.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    if change == 'none':
        assert audit(cell)['passed']
    else:
        with pytest.raises(AssertionError):
            audit(cell)


@pytest.mark.parametrize('change', ['none', 'not_preempted', 'early_release'])
def test_real_preemption_evidence_required(tmp_path, change):
    source = SOURCE.parents[1] / 'generation-shared-preemption-20261012a/two'
    cell = tmp_path / 'cell'
    shutil.copytree(source, cell)
    if change != 'none':
        p = next((cell / 'events').glob('handoff-*-SCHEDULER.jsonl'))
        records = [json.loads(line) for line in p.read_text().splitlines()]
        for row in records:
            if row['event'] == 'protected_preempt_after':
                if change == 'not_preempted':
                    row['status'] = 'RUNNING'
                else:
                    row['refs'] = {k: 0 for k in row['refs']}
        p.write_text('\n'.join(json.dumps(r) for r in records)+'\n')
    if change == 'none':
        assert audit(cell, preemption=True)['passed']
    else:
        with pytest.raises(AssertionError):
            audit(cell, preemption=True)
