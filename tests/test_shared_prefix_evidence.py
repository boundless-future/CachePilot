"""Reject missing/incorrect evidence rather than relying on saved pass flags."""
from copy import deepcopy
import pytest
from scripts.audit_shared_prefix_trace import shared_evidence


def evidence():
    return [dict(event='protection_prepared', wire_id='w', refs_before=[1, 0],
        refs_after=[2, 1], acquired_pins=2, shared_blocks_before=1),
        dict(event='scheduler_built', wire_id='w', source='protection', batch_id='b'),
        dict(event='protection_rollback', reason='zero_token_step')]


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
