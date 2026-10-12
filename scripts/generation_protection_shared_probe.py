"""Diagnostic receipt gate with generation-aware shared lifetime accounting."""
from scripts.protection_shared_cancel_probe import SharedCancelProbeConnector
from scripts.protection_preemption_probe import ProtectedPreemptionConnector
from scripts.generation_protection_connector import GenerationProtectionConnector


class GenerationSharedCancelProbeConnector(SharedCancelProbeConnector, GenerationProtectionConnector):
    pass


class GenerationSharedPreemptionProbeConnector(ProtectedPreemptionConnector, GenerationProtectionConnector):
    pass
