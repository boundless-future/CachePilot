"""Native callback ownership when another owner already holds prefix blocks."""
from types import SimpleNamespace as NS
import pytest
pytest.importorskip('lmcache')
from test_bounded_protection import case
from test_generation_protection_connector import wired, step
from scripts.shared_prefix_connector import SharedPrefixConnector, SharedPrefixServiceProtection
from scripts.shared_prefix_protection import SharedPrefixProtection
from scripts.protection_connector import HandoffWorkerMetadata


@pytest.mark.parametrize('action', ['success', 'failure', 'zero', 'cancel_before_receipt'])
def test_shared_owner_through_bound_callbacks(wired, action):
    obj, helper, scheduler, blocks, transport, meta = wired
    shared = blocks[:8]
    helper.pool.touch(shared)
    helper.prepare(scheduler)
    if not isinstance(helper, SharedPrefixServiceProtection):
        assert not helper.prepared
        helper.pool.free_blocks(shared)
        return
    assert all(b.ref_cnt == 2 for b in shared)
    built = obj.build_connector_meta(step(0 if action == 'zero' else 512))
    if action != 'zero':
        batch, = built.cachepilot_handoffs_v1
        if action == 'cancel_before_receipt':
            helper.pool.free_blocks(shared)
        output = NS(kv_cache_events=None, finished_recving=None,
            kv_connector_worker_meta=HandoffWorkerMetadata({'old': 1},
                {'old'} if action == 'failure' else set(), {'old': batch['batch_id']}))
        obj.update_connector_output(output)
        with pytest.raises(ValueError):
            obj.update_connector_output(output)
    assert all(b.ref_cnt == (0 if action == 'cancel_before_receipt' else 1) for b in shared)
    assert not helper.prepared and not helper.inflight
    if action != 'cancel_before_receipt':
        helper.pool.free_blocks(shared)
    assert helper.pool.get_num_free_blocks() == 128


def test_admission_and_probe_mro():
    obj = object.__new__(SharedPrefixConnector)
    with pytest.raises(ValueError, match='shared-prefix'):
        obj._validate_protection({'cachepilot.generation_protection': True})
    obj._validate_protection({'cachepilot.generation_protection': True,
                             'cachepilot.shared_prefix_protection': True})
    with pytest.raises(RuntimeError, match='independent service'):
        object.__new__(SharedPrefixProtection).bind(None)
    from scripts import shared_prefix_probe as probes
    for name in ('SharedPrefixProbeConnector', 'SharedPrefixReceiptProbeConnector',
                 'SharedPrefixCancelProbeConnector', 'SharedPrefixPreemptionProbeConnector'):
        cls = getattr(probes, name)
        assert cls._make_bounded_protection is SharedPrefixConnector._make_bounded_protection
        assert cls._validate_protection is SharedPrefixConnector._validate_protection
