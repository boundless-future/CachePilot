import copy
import json
from pathlib import Path
import shutil
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_shared_strategy_matrix import CASES, configure, order, check_admission_sources, ADDITIONS, REVIEWED_CHANGES
from audit_shared_strategy_matrix import aggregate, audit_one
ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'docs/experiments/2026-10-12/generation-strategy-validation-evidence/generation-strategy-validation-pilot-20261012a'


def test_case_budget_parameters_and_rotation():
    original=json.loads((ROOT/'configs/protection-zero.json').read_text())
    saved=copy.deepcopy(original)
    for name,case in CASES.items():
        c=configure(original,case);e=c['kv_connector_extra_config']
        assert c['kv_connector']=='SharedPrefixConnector' and e['cachepilot.shared_prefix_protection'] is True
        assert e['cachepilot.protection.max_chunks']=={'zero':0,'one':1,'two':2}[case['mode']]
        assert e['lmcache.mp.lazy_offload_horizon_steps']==case['horizon']
        assert e['lmcache.mp.lazy_offload_max_drain_per_step']==case['drain']
    assert original==saved
    assert [order(i)[0] for i in range(3)]==['default-zero','selected-zero','shared-one']


@pytest.mark.parametrize('change',['none','runtime','missing','extra'])
def test_runtime_frozen_and_harness_only_changes(change):
    old={'scripts/shared_prefix_protection.py':'old',**{p:'old' for p in REVIEWED_CHANGES}}
    new={**old,**{p:'new' for p in REVIEWED_CHANGES|ADDITIONS}}
    if change=='runtime':new['scripts/shared_prefix_protection.py']='new'
    if change=='missing':new.pop('scripts/shared_prefix_protection.py')
    if change=='extra':new['scripts/unlisted.py']='new'
    if change=='none':check_admission_sources(old,new)
    else:
        with pytest.raises(ValueError):check_admission_sources(old,new)


@pytest.mark.parametrize('change',['none','missing','duplicate','failed','pilot'])
def test_no_partial_matrix_aggregate(change):
    rows=[dict(case=n,repeat=i,passed=True,ttft_p95_ms=100+i,h2d_bytes=1,d2h_bytes=2,prefill_tokens=3,committed=1)
          for n in CASES for i in range(3)]
    if change=='missing':rows.pop()
    if change=='duplicate':rows[-1]=rows[0]
    if change=='failed':rows[0]['passed']=False
    if change=='pilot':rows=[r for r in rows if r['repeat']==0]
    assert bool(aggregate(rows))==(change=='none')


@pytest.mark.parametrize('change',['none','closed','early_end','latency','runtime_error','hotpath','config','repeat'])
def test_real_zero_archive_gates_ignore_saved_success(tmp_path,change):
    cell=tmp_path/'selected-zero-r0';shutil.copytree(ARCHIVE/cell.name,cell)
    # Controlled fixture conversion of config identity only; raw measurements unchanged.
    c=configure(json.loads((ROOT/'configs/protection-zero.json').read_text()),CASES['selected-zero'])
    (cell/'config.json').write_text(json.dumps(c))
    if change in ('closed','early_end'):
        p=cell/'generation-summary.json';d=json.loads(p.read_text())
        if change=='closed':d['pending_rpc']=1
        else:d['generations'][0]['end_state']='pending'
        p.write_text(json.dumps(d))
    if change in ('latency','repeat'):
        p=cell/'result.json';d=json.loads(p.read_text());d['ttft_ms']['p95']*=0.5 if change=='latency' else 1
        if change=='repeat':d['repeat']=4
        p.write_text(json.dumps(d))
    if change=='runtime_error':
        with (cell/'vllm.log').open('a') as f:f.write('\nERROR unexpected runtime failure\n')
    if change=='hotpath':(cell/'extra.jsonl').write_text(json.dumps(dict(event='protection_prepared'))+'\n')
    if change=='config':(cell/'config.json').write_text('{}')
    signature=json.loads((ARCHIVE/'plan.json').read_text())['signature']
    trace=json.loads((cell/'trace.json').read_text())
    assert audit_one(cell,'selected-zero',0,signature,trace)['passed']==(change=='none')


def test_saved_success_cannot_promote_actual_zero_protection(tmp_path):
    cell=tmp_path/'shared-one-r0';shutil.copytree(ARCHIVE/'selected-one-r0',cell)
    c=configure(json.loads((ROOT/'configs/protection-zero.json').read_text()),CASES['shared-one'])
    (cell/'config.json').write_text(json.dumps(c))
    p=cell/'result.json';result=json.loads(p.read_text());result['passed']=True;p.write_text(json.dumps(result))
    signature=json.loads((ARCHIVE/'plan.json').read_text())['signature']
    trace=json.loads((cell/'trace.json').read_text())
    row=audit_one(cell,'shared-one',0,signature,trace)
    assert row['checks']['result'] and row['checks']['resources'] and row['checks']['generations']
    assert not row['checks']['coverage'] and not row['passed']
