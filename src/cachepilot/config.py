"""Explicit configuration for the admitted synchronous, single-worker stack.

These parameters reproduce existing development settings, not a performance
recommendation. Upstream constructors still enforce their full runtime guards.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SharedPrefixConfig:
    max_chunks: int = 0
    horizon_steps: float = 5.0
    max_drain_per_step: int = 1
    idle_grace_seconds: float = 15.0
    host: str = "tcp://127.0.0.1"
    port: int = 5555

    def __post_init__(self):
        if type(self.max_chunks) is not int or self.max_chunks not in (0, 1, 2):
            raise ValueError("max_chunks must be 0, 1, or 2")
        if type(self.max_drain_per_step) is not int or self.max_drain_per_step < 1:
            raise ValueError("max_drain_per_step must be a positive integer")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be in 1..65535")
        for name, positive in (("horizon_steps", True), ("idle_grace_seconds", False)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
                raise ValueError(f"Invalid {name}")
        if not isinstance(self.host, str) or not self.host.startswith("tcp://") or not self.host[6:]:
            raise ValueError("host must use the tcp:// transport")

    def kv_transfer_config(self):
        return {
            "kv_connector": "SharedPrefixConnector",
            "kv_connector_module_path": "cachepilot.integration.vllm.shared_prefix",
            "kv_role": "kv_both",
            "kv_load_failure_policy": "recompute",
            "kv_connector_extra_config": {
                "lmcache.mp.host": self.host,
                "lmcache.mp.port": self.port,
                "lmcache.mp.lazy_offload": True,
                "lmcache.mp.lazy_offload_policy": "EVICTION_AWARE",
                "lmcache.mp.lazy_offload_horizon_steps": self.horizon_steps,
                "lmcache.mp.lazy_offload_max_drain_per_step": self.max_drain_per_step,
                "lmcache.mp.lazy_offload_max_deferral_seconds": 0.0,
                "cachepilot.protection.enabled": True,
                "cachepilot.protection.max_chunks": self.max_chunks,
                "cachepilot.idle_finalize": True,
                "cachepilot.idle_finalize_grace_seconds": self.idle_grace_seconds,
                "cachepilot.generation_ledger": True,
                "cachepilot.generation_protection": True,
                "cachepilot.shared_prefix_protection": True,
            },
        }


@dataclass(frozen=True)
class WholeStepConfig(SharedPrefixConfig):
    """Explicit whole-step pilot configuration; never the default."""

    def kv_transfer_config(self):
        result = super().kv_transfer_config()
        result.update(
            kv_connector="WholeStepConnector",
            kv_connector_module_path="cachepilot.integration.vllm.whole_step",
        )
        result["kv_connector_extra_config"]["cachepilot.whole_step_protection"] = True
        return result
