import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('native_identity',Path(__file__).resolve().parents[1]/'docs/experiments/2026-10-13/audit_native_call_identity.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def fixture():
    c=dict(request_id='r',step=1,serial=1,monotonic_ns=20,success=True,full_sequence_must_fit=False,
        new_blocks=1,touched_free_blocks=0,reserved_blocks=0,watermark=0,num_new_tokens=1,
        num_new_computed_tokens=0,num_external_computed_tokens=0,demands=[dict(required=1,free=3)])
    return [c],[dict(monotonic_ns=10)],[dict(step=1,monotonic_ns=30)],[dict(request_id='r')]


def test_original_passes():
    assert module.check(*fixture())['passed']


def test_before_window_and_wrong_identity_rejected():
    c,w,a,g=fixture();c[0].update(monotonic_ns=9,request_id='unrelated')
    r=module.check(c,w,a,g)
    assert not r['checks']['actual_request_identity'] and not r['checks']['exact_step_time_interval']


def test_missing_full_sequence_gate_rejected():
    c,w,a,g=fixture();c[0]['full_sequence_must_fit']=True
    assert not module.check(c,w,a,g)['checks']['native_full_sequence_shape']


def test_negative_count_rejected():
    c,w,a,g=fixture();c[0]['new_blocks']=-1
    assert not module.check(c,w,a,g)['checks']['nonnegative_native_fields']
