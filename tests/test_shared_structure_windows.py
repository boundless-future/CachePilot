from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_shared_structure_windows import classify


def fixture():
    w=dict(reserve=100,free=120,budget=32,used=0)
    c=dict(live=False,finished=True,inflight=False,protected=False,start=0,end=512,skip=0,
        block_ids=[list(range(32))],blocks=[dict(id=i,tracked=True,rank=i,refs=0,null=False,hash_matches=True) for i in range(32)])
    return w,c


def test_budget_capacity_is_separate_from_minimum_capacity():
    w,c=fixture()
    assert classify(w,c)=='selected_budget_capacity'
    w['budget']=16
    assert classify(w,c)=='visible_checks_satisfied'
    w['free']=115
    assert classify(w,c)=='minimum_capacity'


def test_shared_head_does_not_exclude_other_pressure_blocks():
    w,c=fixture();w['free']=140;c['blocks'][0].update(rank=None,refs=2)
    assert classify(w,c)=='visible_checks_satisfied'
    for block in c['blocks']:block['rank']=None
    assert classify(w,c)=='outside_pressure_ranks'


def test_window_not_a_final_token_proof():
    w,c=fixture();w['free']=140
    c['blocks'][3]['hash_matches']=False
    assert classify(w,c)=='block_or_hash_ineligible'
    c['live']=True
    assert classify(w,c)=='request_ineligible'
