"""Explicit diagnostic host exception after protection commit, before handoff."""
import os
from scripts.generation_protection_connector import GenerationProtectionConnector


class GenerationProtectionFaultProbeConnector(GenerationProtectionConnector):
    def _apply_protection(self, metadata, output):
        result = super()._apply_protection(metadata, output)
        helper = self._bounded
        if helper and helper.committed_this_step and not getattr(self, '_fault_injected', False):
            if os.environ.get('CACHEPILOT_GENERATION_FAULT') != 'after_commit':
                raise RuntimeError('Explicit after_commit fault opt-in required')
            self._fault_injected = True
            self._handoff_record('generation_fault_injected', point='after_commit',
                targets=[dict(**helper.inflight[rid].identity,
                    native_inflight=helper.manager._requests.has_in_flight(rid),
                    ref_counts=[b.ref_cnt for b in helper.inflight[rid].blocks])
                    for rid in sorted(helper.committed_this_step)])
            raise RuntimeError('CACHEPILOT_INJECTED_AFTER_PROTECTION_COMMIT')
        return result
