"""Whole-step adapter preserves native receipt and shared reference ownership."""
from types import SimpleNamespace as NS
import pytest

pytest.importorskip('lmcache')
pytest.importorskip('vllm')
from cachepilot_test_support.pool import case
from cachepilot_test_support.connector import wired, step
from cachepilot.integration.vllm.whole_step import (
    WholeStepConnector,
    WholeStepServiceProtection,
)
from cachepilot.integration.vllm.protection import HandoffWorkerMetadata
from cachepilot.runtime.whole_step_protection import WholeStepProtection


def test_service_gate_does_not_enable_cpu_candidate():
    obj = object.__new__(WholeStepConnector)
    with pytest.raises(ValueError, match='whole-step'):
        obj._validate_protection({'cachepilot.generation_protection': True})
    obj._validate_protection({'cachepilot.generation_protection': True,
                             'cachepilot.whole_step_protection': True})
    with pytest.raises(RuntimeError):
        object.__new__(WholeStepProtection).bind(None)
    helper = object.__new__(WholeStepServiceProtection)
    helper.connector = NS(_whole_step_admission=True)
    with pytest.raises(TypeError, match='explicit connector gate'):
        helper.bind(None)


@pytest.mark.parametrize('wired', ['whole-step'], indirect=True)
@pytest.mark.parametrize('action', ['success', 'failure', 'zero'])
def test_whole_step_bound_native_receipts_preserve_shared_owner(wired, action):
    obj, helper, scheduler, blocks, transport, meta = wired
    shared = blocks[:8]
    helper.pool.touch(shared)
    helper.prepare(scheduler)
    assert helper.prepared
    assert all(b.ref_cnt == 2 for b in shared)
    built = obj.build_connector_meta(step(0 if action == 'zero' else 512))
    if action != 'zero':
        batch, = built.cachepilot_handoffs_v1
        receipt = NS(kv_cache_events=None, finished_recving=None,
            kv_connector_worker_meta=HandoffWorkerMetadata({'old': 1},
                {'old'} if action == 'failure' else set(), {'old': batch['batch_id']}))
        obj.update_connector_output(receipt)
        with pytest.raises(ValueError):
            obj.update_connector_output(receipt)
    assert not helper.prepared and not helper.inflight
    assert all(b.ref_cnt == 1 for b in shared)
    helper.pool.free_blocks(shared)
    assert helper.pool.get_num_free_blocks() == 128
