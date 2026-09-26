"""Allocation signal ablation with upstream drain timing and lifecycle intact."""
from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector
from scripts.allocation_pressure import install_allocation_pressure


class AllocationSignalMixin:
    def bind_gpu_block_pool(self, gpu_block_pool):
        super().bind_gpu_block_pool(gpu_block_pool)
        if not self.lazy_offload:
            raise ValueError('Allocation signal ablation requires lazy offload')
        policy = self._lazy_offload_manager._policy
        if policy.__class__.__name__ != 'EvictionAwareStoreQueue':
            raise RuntimeError(f'Unsupported allocation policy: {type(policy).__name__}')
        self._pressure = install_allocation_pressure(
            gpu_block_pool, policy, getattr(self, '_observe_pressure', None))

    def build_connector_meta(self, scheduler_output):
        self._pressure.begin_step()
        return super().build_connector_meta(scheduler_output)


class AllocationConnector(AllocationSignalMixin, LMCacheMPConnector):
    pass
