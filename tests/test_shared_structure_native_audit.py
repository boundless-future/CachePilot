from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_shared_structure_native import audit_calls


def fixture():
    step=dict(step=1,reserve=100,allocated=4,monotonic_ns=30)
    call=dict(serial=1,step=1,full_reserve=100,monotonic_ns=20,success=True,error=None,
        free_before=32,free_after=26,new_blocks=4,touched_free_blocks=2,
        demands=[dict(required=6,free=32)],reserved_blocks=0,watermark=0,
        full_sequence_must_fit=False,num_new_tokens=0,num_external_computed_tokens=64)
    return [call],[step]


def test_mixed_capacity_not_equal_to_only_new_blocks():
    checks,summary=audit_calls(*fixture())
    assert all(checks.values())
    assert summary['zero_ref_touch_consumption']==2
    assert summary['zero_compute_calls_with_new_blocks']==1


def test_omitted_touch_and_understated_demand_fail():
    c,s=fixture()
    c[0]['touched_free_blocks']=0
    checks,_=audit_calls(c,s)
    assert not checks['native_full_attention_consumption']
    assert not checks['native_successful_demand']


def test_lost_step_allocation_or_stale_serial_fails():
    c,s=fixture()
    s[0]['allocated']=5; c[0]['serial']=2
    checks,_=audit_calls(c,s)
    assert not checks['native_no_unaccounted_gross'] and not checks['native_serial_sequence']


def test_rejected_native_reservation_has_no_side_effect():
    c,s=fixture()
    c[0].update(success=False,new_blocks=0,touched_free_blocks=0,free_after=32,reserved_blocks=27)
    s[0]['allocated']=0
    assert all(audit_calls(c,s)[0].values())
    c[0]['reserved_blocks']=26
    assert not audit_calls(c,s)[0]['native_rejection_capacity_gate']


def test_full_sequence_rejection_uses_its_own_gate():
    c,s=fixture()
    c[0].update(success=False,new_blocks=0,touched_free_blocks=0,free_after=32,
        full_sequence_must_fit=True,demands=[dict(required=33,free=32)])
    s[0]['allocated']=0
    assert all(audit_calls(c,s)[0].values())


def test_missing_calls_and_native_exceptions_are_not_success():
    assert not audit_calls([],[])[0]['native_calls_observed']
    c,s=fixture(); c[0]['error']='test exception'
    assert not audit_calls(c,s)[0]['native_no_call_error']
