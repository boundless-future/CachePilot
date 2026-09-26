"""Heavy diagnostics for allocation ablation; never use for timing comparisons."""
from scripts.allocation_connector import AllocationSignalMixin
from scripts.decision_connector import DecisionConnector


class AllocationDiagnosticConnector(AllocationSignalMixin, DecisionConnector):
    def _observe_pressure(self, **data):
        self._record('allocation_signal', **data)
