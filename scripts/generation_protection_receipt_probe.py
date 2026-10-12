"""Existing failed-after-success diagnostic on the explicit generation stack."""
from scripts.protection_receipt_probe import ReceiptProbeConnector
from scripts.generation_protection_connector import GenerationProtectionConnector


class GenerationProtectionReceiptProbeConnector(ReceiptProbeConnector, GenerationProtectionConnector):
    pass
