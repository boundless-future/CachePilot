"""Explicit opt-in candidate; normal ProtectionConnector remains unchanged."""
import hashlib
import inspect
import math
from pathlib import Path

from cachepilot.integration.vllm.protection import ProtectionConnector
from cachepilot.runtime.idle import install_idle_scheduler


class IdleFinalizeConnector(ProtectionConnector):
    def __init__(self, vllm_config, role, kv_cache_config=None):
        extra = vllm_config.kv_transfer_config.kv_connector_extra_config
        self._idle_finalize = extra.get('cachepilot.idle_finalize', False)
        self._idle_grace_seconds = float(extra.get('cachepilot.idle_finalize_grace_seconds', 0))
        if type(self._idle_finalize) is not bool:
            raise ValueError('cachepilot.idle_finalize must be boolean')
        if not math.isfinite(self._idle_grace_seconds) or self._idle_grace_seconds < 0:
            raise ValueError('cachepilot.idle_finalize_grace_seconds must be nonnegative')
        if self._idle_finalize and (vllm_config.scheduler_config.async_scheduling
                or vllm_config.speculative_config is not None or vllm_config.lora_config is not None):
            raise ValueError('Idle finalization requires synchronous text-only scheduling')
        super().__init__(vllm_config, role, kv_cache_config)

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        if not self._idle_finalize:
            return
        from vllm.v1.core.sched.scheduler import Scheduler
        from vllm.v1.engine.core import EngineCoreProc
        from lmcache.integration.vllm import lazy_offload_state
        from lmcache.integration.vllm.lazy_offload_policy import eviction_aware
        for obj, expected in (
            (Scheduler, 'c1db45f3bbad3a875dd8638331b8870c4380ac1ab4734265ba5c863833a82884'),
            (EngineCoreProc, '268bf40534a853812867ae8e80040f190983cb364b41182b55969aace20c93f5'),
            (lazy_offload_state, '1785139617dde03861341dba16528be252bba60d41aa998eee0c8a05717d3289'),
            (eviction_aware, 'f75df38b9f3bf3eaa364b7ec3dbcb4fc1229eec54ad3db35509591a19a150f33')):
            if hashlib.sha256(Path(inspect.getsourcefile(obj)).read_bytes()).hexdigest() != expected:
                raise RuntimeError('Unsupported idle finalization source')
        install_idle_scheduler(Scheduler)
        from cachepilot.runtime.idle import install_idle_core
        install_idle_core(EngineCoreProc)
