import json
from pathlib import Path
import shutil
import pytest
from scripts.audit_capacity_diagnostic import audit_cell, read, VALIDATION_SHA
from scripts.analyze_capacity_windows import analyze

BASE=Path(__file__).resolve().parents[1]/'docs/experiments/2026-10-12/capacity-diagnostic-evidence/capacity-diagnostic-20261012a'


def test_archive_reproduces_referenced_shared_head():
    result=analyze(BASE/'validation')
    assert result['nonnegative_and_budget']==203
    assert result['reference_rejections']==147
    assert result['referenced_block_ids']=={'1':147}
    assert result['referenced_offsets']=={'0':147}


@pytest.mark.parametrize('mutation', ['reserve','missing_step'])
def test_corrupted_boundary_is_rejected(tmp_path,mutation):
    cell=tmp_path/'validation';shutil.copytree(BASE/'validation',cell)
    path=next(p for p in (cell/'handoff').glob('*.jsonl') if 'SCHEDULER' in p.name)
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    i=next(i for i,r in enumerate(rows) if r.get('stage')=='boundary' and r['reserve'] is not None)
    if mutation=='reserve':
        rows[i]['reserve']+=1;path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        r=audit_cell(cell,read(BASE/'plan.json')['signature'],VALIDATION_SHA)
        assert not r['passed'] and not r['checks']['diagnostic']
    else:
        rows.pop(i);path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        with pytest.raises(ValueError,match='boundary'):analyze(cell)
