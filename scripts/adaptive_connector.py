"""Experimental horizon adapter without per-step diagnostic logging."""
from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector
from scripts.horizon_policy import HorizonSettings, install_horizon


class AdaptiveConnector(LMCacheMPConnector):
    def __init__(self, vllm_config, role, kv_cache_config=None):
        extra = vllm_config.kv_transfer_config.kv_connector_extra_config
        self._horizon_settings = HorizonSettings(
            base=float(extra.get('lmcache.mp.lazy_offload_horizon_steps', 2.5)),
            wide=float(extra.get('cachepilot.wide_horizon', 5.0)),
            threshold=float(extra.get('cachepilot.block_threshold', 16.0)))
        super().__init__(vllm_config, role, kv_cache_config)

    def bind_gpu_block_pool(self, gpu_block_pool):
        super().bind_gpu_block_pool(gpu_block_pool)
        if not self.lazy_offload:
            raise ValueError('AdaptiveConnector requires lazy offload')
        policy = self._lazy_offload_manager._policy
        if policy.__class__.__name__ != 'EvictionAwareStoreQueue':
            raise RuntimeError(f'Unsupported adaptive policy: {type(policy).__name__}')
        install_horizon(policy, self._horizon_settings)
