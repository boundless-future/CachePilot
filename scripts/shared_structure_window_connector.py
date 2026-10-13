"""Read-only window recording without synchronizing KV or allocator probes."""
from scripts.shared_prefix_connector import SharedPrefixConnector
from scripts.shared_prefix_window_probe import WindowProtection


class SharedStructureWindowConnector(SharedPrefixConnector):
    def _make_bounded_protection(self, config):
        return WindowProtection(self, config)
