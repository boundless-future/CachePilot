from types import SimpleNamespace as NS
import pytest
from cachepilot.runtime.admission import ConservativeAdmission
from cachepilot.runtime.whole_step import whole_step_envelope, WholeStepAdmission


def scheduler(remote=False):
    req = NS(request_id='r', num_tokens_with_spec=64, num_computed_tokens=0)
    return NS(requests={'r':req}, max_num_scheduled_tokens=512,
              _inflight_prefills=[req] if remote else [], kv_cache_manager=NS(watermark_blocks=3))


@pytest.mark.parametrize('remote',[False,True])
def test_explicit_remote_membership_retains_extra_allowance(remote):
    s=scheduler(remote)
    e=whole_step_envelope(s)
    assert e.request_blocks==(('r',5),) and e.remote_blocks==(5 if remote else 0)
    assert e.reserve==(29 if remote else 24)
    assert e.reserve <= ConservativeAdmission().reserve(s)


def test_missing_remote_membership_fails_closed():
    s=scheduler();del s._inflight_prefills
    assert whole_step_envelope(s) is None


@pytest.mark.parametrize('field,value',[('mm_features',['image']),('lora_request',object()),
    ('num_in_flight_tokens',1),('spec_token_ids',[1])])
def test_unsupported_request_fails_closed(field,value):
    s=scheduler();setattr(s.requests['r'],field,value)
    assert WholeStepAdmission().reserve(s) is None


@pytest.mark.parametrize('kind',['duplicate','unknown'])
def test_inconsistent_membership_is_rejected(kind):
    s=scheduler(True)
    s._inflight_prefills = s._inflight_prefills*2 if kind=='duplicate' else [NS(request_id='unknown')]
    with pytest.raises(ValueError,match='membership'):whole_step_envelope(s)


def test_computed_footprint_is_not_subtracted_or_clipped():
    s=scheduler();s.requests['r'].num_computed_tokens=128
    assert whole_step_envelope(s).request_blocks==(('r',9),)
    assert WholeStepAdmission().reserve(s)==28
