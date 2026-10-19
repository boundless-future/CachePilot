"""Opt-in service adapter for the whole-step admission candidate.

The runtime candidate remains CPU-only by default.  This adapter is the
explicit experiment gate: it only constructs the service protection object
when the connector, generation ledger, and whole-step flags are all present.
"""

from cachepilot.integration.lmcache.protection import GenerationBoundedProtection
from cachepilot.integration.vllm.generation_protection import GenerationProtectionConnector
from cachepilot.runtime.shared_prefix import SharedPrefixProtection
from cachepilot.runtime.whole_step import WholeStepAdmission


class WholeStepServiceProtection(SharedPrefixProtection):
    admission_policy = WholeStepAdmission()

    def bind(self, kv_manager):
        if not isinstance(self.connector, WholeStepConnector):
            raise TypeError("Whole-step service adapter requires explicit connector gate")
        return GenerationBoundedProtection.bind(self, kv_manager)


class WholeStepConnector(GenerationProtectionConnector):
    """Single-worker experimental Connector for one whole-step pilot cell."""

    def _validate_protection(self, extra):
        super()._validate_protection(extra)
        if extra.get("cachepilot.whole_step_protection") is not True:
            raise ValueError("Explicit whole-step protection opt-in required")

    def _make_bounded_protection(self, config):
        return WholeStepServiceProtection(self, config)
