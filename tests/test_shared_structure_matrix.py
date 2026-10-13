import copy
import json
from pathlib import Path
import shutil
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shared_structure_matrix import CASES, configure, check_admission_sources, ADDITIONS, VALIDATION_SHA
from audit_shared_structure_matrix import audit_one
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'docs/experiments/2026-10-12/real-structure-evidence/real-structure-formal-20261012a'

@pytest.mark.parametrize('change',['none','runtime','missing','extra'])
def test_frozen_runtime(change):
    old={'scripts/shared_prefix_protection.py':'old'}
    new={**old,**{p:'new' for p in ADDITIONS}}
    if change=='runtime':new['scripts/shared_prefix_protection.py']='new'
    if change=='missing':new.pop('scripts/shared_prefix_protection.py')
    if change=='extra':new['scripts/unknown.py']='new'
    if change=='none':check_admission_sources(old,new)
    else:
        with pytest.raises(ValueError):check_admission_sources(old,new)

@pytest.mark.parametrize('change',['none','generation','raw_id','latency','trace','config','preemptions'])
def test_anonymous_raw_evidence_and_preempted_generations(tmp_path,change):
    cell=tmp_path/'selected-zero-r0';shutil.copytree(ARCHIVE/'h5p0-d1-r0',cell)
    (cell/'config.json').write_text(json.dumps(configure(json.loads((ROOT/'configs/protection-zero.json').read_text()),CASES['selected-zero'])))
    signature=json.loads((ARCHIVE/'plan.json').read_text())['signature']
    trace=json.loads((cell/'trace.json').read_text())
    assert signature['trace_sha256']==VALIDATION_SHA and isinstance(trace['events'][0]['prompt'],list)
    if change in ('generation','raw_id'):
        p=cell/'generation-summary.json';d=json.loads(p.read_text())
        if change=='generation':d['generations'][0]['end_state']='pending'
        else:d['generations'][0]['request_id']='invented'
        p.write_text(json.dumps(d))
    if change in ('latency','preemptions'):
        p=cell/'result.json';d=json.loads(p.read_text())
        if change=='latency':d['ttft_ms']['p95']*=0.5
        else:d['vllm_delta']['vllm:num_preemptions_total']=0
        p.write_text(json.dumps(d))
    if change=='trace':
        p=cell/'trace.json';d=json.loads(p.read_text());d['events'][0]['prompt'][0]+=1;p.write_text(json.dumps(d))
    if change=='config':(cell/'config.json').write_text('{}')
    row=audit_one(cell,'selected-zero',0,signature,trace)
    assert row['passed']==(change=='none')
    if change=='none':assert row['generation_count']==62 and row['preemptions']==1
