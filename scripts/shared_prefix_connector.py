"""Explicit service admission for the shared-prefix reference candidate."""
from scripts.shared_prefix_protection import SharedPrefixProtection
from scripts.generation_protection_bridge import GenerationBoundedProtection
from scripts.generation_protection_connector import GenerationProtectionConnector


class SharedPrefixServiceProtection(SharedPrefixProtection):
    def bind(self, kv_manager):
        if not isinstance(self.connector, SharedPrefixConnector):
            raise TypeError('Independent shared-prefix connector required')
        return GenerationBoundedProtection.bind(self, kv_manager)


class SharedPrefixConnector(GenerationProtectionConnector):
    def _validate_protection(self, extra):
        super()._validate_protection(extra)
        if extra.get('cachepilot.shared_prefix_protection') is not True:
            raise ValueError('Explicit shared-prefix protection opt-in required')

    def _make_bounded_protection(self, config):
        return SharedPrefixServiceProtection(self, config)
