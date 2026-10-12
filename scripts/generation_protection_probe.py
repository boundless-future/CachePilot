"""Synchronizing diagnostics only; never select for performance measurements."""
from scripts.protection_probe_connector import ProbeMixin
from scripts.generation_protection_connector import GenerationProtectionConnector


class GenerationProtectionProbeConnector(ProbeMixin, GenerationProtectionConnector):
    pass
