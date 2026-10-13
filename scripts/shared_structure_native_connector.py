"""Native allocation call observations atop unchanged structure diagnostics."""
from scripts.shared_structure_allocation_connector import AllocationWindowProtection, SharedStructureAllocationConnector
from scripts.native_allocation_observer import install_native_allocation_observer


class NativeWindowProtection(AllocationWindowProtection):
    def bind(self, manager):
        super().bind(manager)

        def context():
            if self._boundary is None:
                raise RuntimeError('Native allocation outside scheduler observation')
            return dict(step=self._step, full_reserve=self._boundary['reserve'])

        install_native_allocation_observer(manager, self.record, context)


class SharedStructureNativeConnector(SharedStructureAllocationConnector):
    def _make_bounded_protection(self, config):
        return NativeWindowProtection(self, config)
