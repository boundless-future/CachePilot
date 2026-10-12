"""Explicit diagnostic-only connector; not a performance candidate."""
import os
from scripts.generation_protection_connector import GenerationProtectionConnector
from scripts.generation_protection_bridge import GenerationBoundedProtection
from scripts.capacity_diagnostic_protection import DiagnosticBoundedProtection


class CapacityBoundedProtection(GenerationBoundedProtection, DiagnosticBoundedProtection):
    pass


class CapacityDiagnosticConnector(GenerationProtectionConnector):
    def _make_bounded_protection(self, config):
        if os.environ.get('CACHEPILOT_CAPACITY_DIAGNOSTICS') != '1':
            raise ValueError('Explicit diagnostic environment required')
        return CapacityBoundedProtection(self, config)
