"""Diagnostic variants; service ownership always uses SharedPrefixConnector."""
from scripts.shared_prefix_connector import SharedPrefixConnector
from scripts.protection_probe_connector import ProbeMixin
from scripts.protection_receipt_probe import ReceiptProbeConnector
from scripts.protection_shared_cancel_probe import SharedCancelProbeConnector
from scripts.protection_preemption_probe import ProtectedPreemptionConnector


class SharedPrefixProbeConnector(ProbeMixin, SharedPrefixConnector):
    pass


class SharedPrefixReceiptProbeConnector(ReceiptProbeConnector, SharedPrefixConnector):
    pass


class SharedPrefixCancelProbeConnector(SharedCancelProbeConnector, SharedPrefixConnector):
    pass


class SharedPrefixPreemptionProbeConnector(ProtectedPreemptionConnector, SharedPrefixConnector):
    pass
