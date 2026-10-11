import json

import pytest

from scripts.run_idle_capacity import check_pilot, strict_idle_state


def test_sessions_and_temporary_objects_are_required_and_zero():
    state=dict(active_sessions=0,storage_manager={'l1_manager':{'temporary_count':0}})
    assert strict_idle_state(state)
    state['active_sessions']=1
    assert not strict_idle_state(state)
    state['active_sessions']=0
    state['storage_manager']['l1_manager']['temporary_count']=1
    assert not strict_idle_state(state)
    assert not strict_idle_state({})


def pilot(tmp_path):
    (tmp_path/'plan.json').write_text(json.dumps(dict(stage='pilot',signature={'source':'frozen'})))
    for mode in ('zero','one','two'):
        p=tmp_path/(mode+'-r0');p.mkdir()
        for name in ('result.json','idle-validation.json'):
            (p/name).write_text(json.dumps(dict(passed=True)))


def test_formal_requires_matching_complete_pilot(tmp_path):
    pilot(tmp_path)
    check_pilot(tmp_path,{'source':'frozen'})
    with pytest.raises(AssertionError): check_pilot(tmp_path,{'source':'changed'})
    (tmp_path/'one-r0/idle-validation.json').write_text(json.dumps(dict(passed=False)))
    with pytest.raises(AssertionError): check_pilot(tmp_path,{'source':'frozen'})


def test_missing_pilot_cell_is_not_skipped(tmp_path):
    pilot(tmp_path)
    (tmp_path/'two-r0/result.json').unlink()
    with pytest.raises(FileNotFoundError): check_pilot(tmp_path,{'source':'frozen'})
