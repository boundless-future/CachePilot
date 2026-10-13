"""Generation bridge fixture built on the real pool fixture."""
import pytest
from .pool import case
from cachepilot.integration.lmcache.protection import GenerationProtectionBridge, ProtectionGenerationLedger, GenerationBoundedProtection
from cachepilot.integration.lmcache.protocol import wire_id

@pytest.fixture
def combined(case):
    helper, scheduler, blocks, rows = case
    candidate = object.__new__(GenerationBoundedProtection)
    candidate.__dict__.update(helper.__dict__)
    helper = candidate
    meta = helper.policy._pending['old'][0].store_metadata
    helper.manager.on_request_reset('old')
    ledger = ProtectionGenerationLedger()
    bridge = GenerationProtectionBridge(helper.manager, ledger)
    helper.manager = bridge
    ledger.register('old', wire_id(meta))
    bridge.on_request_arrived('old')
    bridge.add_store_candidate(meta)
    bridge.on_request_finished('old')
    return helper, scheduler, blocks, bridge, ledger, meta
