"""First CachePilot mechanism: pressure-aware horizon selection.

This is deliberately small and opt-in. It wraps LMCache's pinned
EVICTION_AWARE policy without changing its hash, pin, completion, or cleanup
semantics. High allocation pressure widens the look-ahead horizon; idle steps
restore the configured base horizon.
"""
from dataclasses import replace
import os
from pathlib import Path

from scripts.decision_connector import DecisionConnector


class AdaptiveConnector(DecisionConnector):
    def bind_gpu_block_pool(self, gpu_block_pool):
        super().bind_gpu_block_pool(gpu_block_pool)
        policy = self._lazy_offload_manager._policy
        if policy.__class__.__name__ != 'EvictionAwareStoreQueue':
            raise RuntimeError(f'Unsupported adaptive policy: {type(policy).__name__}')
        base = policy._config.horizon_steps
        wide = float(os.environ.get('CACHEPILOT_ADAPTIVE_WIDE_HORIZON', '5.0'))
        threshold = float(os.environ.get('CACHEPILOT_ADAPTIVE_BLOCK_THRESHOLD', '16'))
        original_drain = policy.drain

        def drain(signals):
            pressure = max(float(signals.new_blocks_allocated),
                           float(signals.est_next_step_blocks))
            horizon = wide if pressure >= threshold else base
            old = policy._config
            policy._config = replace(old, horizon_steps=horizon)
            try:
                result = original_drain(signals)
            finally:
                policy._config = old
            self._record('adaptive_horizon', pressure=pressure, threshold=threshold,
                         base_horizon=base, wide_horizon=wide, selected_horizon=horizon)
            return result

        policy.drain = drain
