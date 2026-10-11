"""Opt-in composition with existing diagnostic probes; no new injection logic."""
from scripts.idle_finalize_connector import IdleFinalizeConnector
from scripts.protection_receipt_probe import ReceiptProbeConnector
from scripts.protection_shared_cancel_probe import SharedCancelProbeConnector


class IdleReceiptProbeConnector(ReceiptProbeConnector, IdleFinalizeConnector):
    pass


class IdleSharedCancelProbeConnector(SharedCancelProbeConnector, IdleFinalizeConnector):
    pass
