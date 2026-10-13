"""Reject missing/incorrect evidence rather than relying on saved pass flags."""
from copy import deepcopy
import pytest
from scripts.audit_shared_prefix_trace import shared_evidence
from scripts.audit_shared_prefix_trace import audit_postlude
import hashlib
import json


def evidence():
    return [dict(event='protection_prepared', wire_id='w', refs_before=[1, 0],
        refs_after=[2, 1], acquired_pins=2, shared_blocks_before=1),
        dict(event='protection_metadata_committed', wire_id='w'),
        dict(event='scheduler_built', wire_id='w', source='protection', batch_id='b'),
        dict(event='protection_prepared', wire_id='v', refs_before=[0, 0],
             refs_after=[1, 1], acquired_pins=2, shared_blocks_before=0),
        dict(event='protection_rollback', wire_id='v', reason='zero_token_step')]


def test_shared_commit_and_zero_step_evidence():
    assert shared_evidence(evidence()) == dict(shared_prepares=1,
        shared_committed_batches=['b'], zero_step_rollbacks=1)


@pytest.mark.parametrize('change', ['refs', 'count', 'pins', 'missing'])
def test_reject_corrupted_shared_evidence(change):
    data = deepcopy(evidence())
    if change == 'refs': data[0]['refs_after'][0] = 1
    if change == 'count': data[0]['shared_blocks_before'] = 2
    if change == 'pins': data[0]['acquired_pins'] = 3
    if change == 'missing': data = data[1:]
    with pytest.raises(AssertionError): shared_evidence(data)


def test_shared_rollback_is_not_misattributed_to_later_unshared_commit():
    data = evidence()
    first = deepcopy(data[0])
    second = dict(first, refs_before=[0, 0], refs_after=[1, 1], shared_blocks_before=0)
    result = shared_evidence([first, dict(event='protection_rollback', wire_id='w', reason='zero_token_step'),
        second, *data[1:3]])
    assert result['shared_prepares'] == 1 and result['shared_committed_batches'] == []


@pytest.mark.parametrize('corrupt', [None, 'restore', 'count', 'output', 'identity'])
def test_postlude_has_exact_requests_and_original_protected_prefix(corrupt):
    ids = list(range(257))
    key = hashlib.sha256(json.dumps(['', ids[:256]], separators=(',', ':')).encode()).hexdigest()
    batch = dict(event='scheduler_built', source='protection', prefix_sha256=key, batch_id='b')
    def request(label, prompt):
        return dict(label=label, payload=dict(prompt=prompt, max_tokens=1, temperature=0, seed=42, ignore_eos=True),
            response=dict(usage=dict(prompt_tokens=len(prompt), completion_tokens=1)))
    data = dict(targets={key:batch}, requests=[request(f'evict-{i}', ids[:224]) for i in range(160)] +
        [request('restore-'+key, ids)] + [request(f'final-{i}', ids[:224]) for i in range(160)])
    if corrupt == 'restore': data['requests'][160]['payload']['prompt'][0] = 999
    if corrupt == 'count': data['requests'].pop()
    if corrupt == 'output': data['requests'][0]['response']['usage']['completion_tokens'] = 0
    if corrupt == 'identity': data['targets'][key] = dict(batch, batch_id='other')
    if corrupt:
        with pytest.raises(AssertionError): audit_postlude(data, [batch])
    else:
        assert audit_postlude(data, [batch])['requests'] == 321
