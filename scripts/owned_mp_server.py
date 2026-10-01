"""Opt-in identity guard on the real LMCache MP LOOKUP/RETRIEVE handlers.

This bridge checks the existing IPC key's request generation. It does not
replace LMCache's anonymous read locks or prove asynchronous DMA completion.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import threading
import time

import msgspec

GENERATION_CONFIG = "cachepilot.lookup_generation.v1"
SOURCE_HASHES = {
    "lookup.py": "cdd6917c6b3e1aed283313ca7a696ded9e8ad0e7309df7ce4afb0285103b7866",
    "lmcache_driven_transfer.py": "18eebd07c8a42e8f869b1551d2bad520d471518592f0603ba28c5c51ddf3ff6d",
}


class NativeMarkerV1(msgspec.Struct, frozen=True):
    generation: str
    request_id: str
    worker_id: int
    instance_id: int


def generation(key):
    value = (key.request_configs or {}).get(GENERATION_CONFIG)
    if not isinstance(value, str) or len(value) != 32:
        raise ValueError("Missing or invalid CachePilot lookup generation")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("Invalid CachePilot lookup generation") from exc
    return value


def install_guard(directory):
    from lmcache.v1.multiprocess.modules.lookup import LookupModule
    from lmcache.v1.multiprocess.modules.lmcache_driven_transfer import (
        LMCacheDrivenTransferModule,
    )
    from lmcache.v1.multiprocess.native_completion import submit_callback_to_stream

    for cls in (LookupModule, LMCacheDrivenTransferModule):
        source = Path(inspect.getsourcefile(cls))
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != SOURCE_HASHES[source.name]:
            raise RuntimeError(f"Unsupported installed LMCache source: {source}")

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"owned-mp-{os.getpid()}.jsonl"
    gate = threading.RLock()
    identities = {}
    original_lookup = LookupModule.lookup
    original_end = LookupModule.end_session
    original_init = LMCacheDrivenTransferModule.__init__
    original_retrieve = LMCacheDrivenTransferModule.retrieve

    def record(event, request_id, **fields):
        row = dict(event=event, request_id=request_id, pid=os.getpid(),
                   monotonic_ns=time.monotonic_ns(), **fields)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    @functools.wraps(original_lookup)
    def lookup(self, key, *args, **kwargs):
        nonce = generation(key)
        with gate:
            if key.request_id in identities:
                raise ValueError("Overlapping LOOKUP generation")
            identity = (nonce, key.model_name, key.world_size, key.cache_salt,
                        key.token_ids, key.end)
            result = original_lookup(self, key, *args, **kwargs)
            identities[key.request_id] = identity
            record("lookup_registered", key.request_id, generation=nonce,
                   end=key.end, num_kv_readers=key.num_kv_readers)
            return result

    @functools.wraps(original_end)
    def end(self, request_id, *args, **kwargs):
        with gate:
            result = original_end(self, request_id, *args, **kwargs)
            identities.pop(request_id, None)
            record("session_ended", request_id)
            return result

    @functools.wraps(original_init)
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.register_host_func("cachepilot.retrieve.marker.v1",
            lambda marker: record("retrieve_native_completed", marker.request_id,
                generation=marker.generation, worker_id=marker.worker_id,
                instance_id=marker.instance_id), NativeMarkerV1)

    @functools.wraps(original_retrieve)
    def retrieve(self, key, instance_id, gpu_block_ids, event_ipc_handle,
                 skip_first_n_tokens=0):
        with gate:
            identity = identities.get(key.request_id)
            try:
                nonce = generation(key)
            except ValueError:
                nonce = None
            expected = (nonce, key.model_name, key.world_size,
                        key.cache_salt, key.token_ids)
            if (identity is None or key.worker_id is None
                    or not 0 <= key.worker_id < key.world_size
                    or identity[:5] != expected or key.start < 0
                    or key.start >= key.end or key.end > identity[5]):
                record("retrieve_rejected_identity", key.request_id,
                       generation=expected[0], worker_id=key.worker_id)
                return b"", False

        entry = self.get_and_touch_context_entry(instance_id)
        if entry is not None:
            context = entry.cache_context
            groups = context.kv_layer_groups_manager.num_kernel_groups
            chunks = (key.end - key.start) // self._ctx.chunk_size
            required = [chunks * context.calculate_num_blocks(
                self._ctx.chunk_size, group) for group in range(groups)]
            if (key.start % self._ctx.chunk_size or
                    key.end % self._ctx.chunk_size or
                    len(gpu_block_ids) != groups or
                    any(len(ids) < need for ids, need in
                        zip(gpu_block_ids, required, strict=True))):
                # No transfer was submitted. Use the installed server's
                # per-instance, generation-aware failed-retrieve claim.
                try:
                    self._release_failed_retrieve_locks(key, instance_id)
                except Exception as exc:
                    record("retrieve_release_failed", key.request_id,
                           error=repr(exc))
                record("retrieve_rejected_blocks", key.request_id,
                       required=required, supplied=list(map(len, gpu_block_ids)))
                return b"", False

        record("retrieve_submitted", key.request_id, generation=expected[0],
               worker_id=key.worker_id, start=key.start, end=key.end,
               instance_id=instance_id)
        result = original_retrieve(self, key, instance_id, gpu_block_ids,
                                   event_ipc_handle, skip_first_n_tokens)
        record("retrieve_rpc_result", key.request_id, succeeded=result[1])
        if result[0] and entry is not None:
            try:
                submit_callback_to_stream(entry.cache_context.cupy_stream,
                    "cachepilot.retrieve.marker.v1", NativeMarkerV1(
                        nonce, key.request_id, key.worker_id, instance_id))
            except Exception as exc:
                record("retrieve_native_marker_failed", key.request_id,
                       error=repr(exc))
        return result

    LookupModule.lookup = lookup
    LookupModule.end_session = end
    LMCacheDrivenTransferModule.__init__ = initialize
    LMCacheDrivenTransferModule.retrieve = retrieve


if __name__ == "__main__":
    from lmcache.cli.main import main

    install_guard(os.environ["CACHEPILOT_OWNED_MP_DIR"])
    sys.argv[0] = "lmcache"
    sys.exit(main())
