"""Opt-in failure-only observation; keeps original idle-finalization guard."""
import json
import os
from pathlib import Path
import time

from scripts.idle_finalize_connector import IdleFinalizeConnector
from scripts.idle_identity_observation import observe


class IdleIdentityProbeConnector(IdleFinalizeConnector):
    def __init__(self, vllm_config, role, kv_cache_config=None):
        directory = os.environ.get('CACHEPILOT_IDLE_IDENTITY_DIR')
        if not directory:
            raise ValueError('Identity probe requires an explicit evidence directory')
        super().__init__(vllm_config, role, kv_cache_config)
        if role.name == 'SCHEDULER':
            from scripts import idle_finalize
            original = idle_finalize.finalize_idle_pending
            if getattr(original, '_identity_observed', False):
                raise ValueError('Identity observer is already installed')
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            def record(row):
                row.update(pid=os.getpid(),monotonic_ns=time.monotonic_ns())
                with (path/'identity-failure.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(row,sort_keys=True)+'\n')
            wrapper = observe(original,record)
            wrapper._identity_observed = True
            idle_finalize.finalize_idle_pending = wrapper
