"""Opt-in, synchronizing KV integrity probe for the pinned single-GPU Qwen setup.

Loaded as an external vLLM connector; never enable for performance measurement.
No installed upstream files are modified. Unsupported layouts fail explicitly.
"""
import hashlib
import json
import os
from pathlib import Path
import time

import torch
from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector


def token_key(ids, salt, end):
    return hashlib.sha256(json.dumps([salt, list(ids[:end])], separators=(',', ':')).encode()).hexdigest()


class DiagnosticConnector(LMCacheMPConnector):
    def register_kv_caches(self, kv_caches):
        super().register_kv_caches(kv_caches)
        self._probe_refs = {}
        self._probe_pending = {}
        self._probe_dir = Path(os.environ['CACHEPILOT_PROBE_DIR'])
        self._probe_dir.mkdir(parents=True, exist_ok=True)
        adapter = self.worker_adapter
        submit_store = adapter.submit_store_request
        submit_retrieve = adapter.submit_retrieve_request

        def store(request_id, op, event, cache_salt='', request_configs=None):
            self._snapshot('before_store', request_id, op, cache_salt)
            return submit_store(request_id, op, event, cache_salt, request_configs)

        def retrieve(request_id, op, event, cache_salt='', request_configs=None):
            result = submit_retrieve(request_id, op, event, cache_salt, request_configs)
            if request_id in adapter.retrieve_futures:
                self._probe_pending[request_id] = (op, cache_salt)
            return result

        adapter.submit_store_request = store
        adapter.submit_retrieve_request = retrieve

    def _record(self, row):
        row.update(monotonic_ns=time.monotonic_ns(), pid=os.getpid())
        with (self._probe_dir / f'kv-{os.getpid()}.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')

    def _snapshot(self, phase, request_id, op, salt):
        adapter = self.worker_adapter
        groups = adapter.engine_group_infos
        if len(groups) != 1 or groups[0].recurrent_state:
            raise RuntimeError('Probe supports one dense attention KV group only')
        span = groups[0].tokens_per_block
        chunk = adapter.lmcache_tokens_per_chunk
        blocks = adapter._block_ids_per_group(op)
        if len(blocks) != 1 or op.start % chunk or op.end % chunk or op.skip_first_n_tokens:
            raise RuntimeError('Probe requires aligned full chunks, one group, and no skipped prefix')
        if len(blocks[0]) * span != op.end - op.start:
            raise RuntimeError('KV block span does not match operation token range')
        torch.cuda.synchronize()
        for offset in range(0, op.end - op.start, chunk):
            begin, end = op.start + offset, op.start + offset + chunk
            key = token_key(op.token_ids, salt, end)
            ids = blocks[0][offset // span:(offset + chunk) // span]
            row = dict(phase=phase, request_id=request_id, salt=salt, start=begin,
                       end=end, token_prefix_sha256=key, block_ids=ids, layers=[])
            for name, tensor in adapter.kv_caches.items():
                # vLLM 0.30's paged CUDA cache is [num_blocks, heads, block, dim]
                # for this Qwen model. Keep the probe layout-agnostic at the
                # physical block boundary; this is an integrity check, not a
                # semantic KV visualizer.
                if tensor.ndim < 3 or tensor.shape[0] <= max(ids, default=-1):
                    raise RuntimeError(f'Unsupported KV layout: {name} {tensor.shape} stride={tensor.stride()}')
                axis = 0
                indices = torch.tensor(ids, device=tensor.device, dtype=torch.long)
                value = tensor.index_select(axis, indices).contiguous().cpu()
                digest = hashlib.sha256(value.view(torch.uint8).numpy().tobytes()).hexdigest()
                ref_key = (key, name)
                ref = self._probe_refs.get(ref_key)
                item = dict(layer=name, shape=list(value.shape), dtype=str(value.dtype),
                            source_shape=list(tensor.shape), sha256=digest,
                            reference_present=ref is not None)
                if ref is not None:
                    # Bit equality, including NaN payload and signed zero.
                    equal = torch.equal(value.view(torch.uint8), ref.view(torch.uint8))
                    item['bitwise_equal'] = equal
                    if not equal:
                        item['different_elements'] = int((value.view(torch.uint8) != ref.view(torch.uint8)).sum())
                        item['max_abs_difference'] = float((value.float() - ref.float()).abs().max())
                elif phase == 'before_store':
                    if len(self._probe_refs) >= 4096:
                        raise RuntimeError('Probe reference budget exceeded')
                    self._probe_refs[ref_key] = value
                row['layers'].append(item)
            self._record(row)

    def get_finished(self, finished_req_ids):
        adapter = self.worker_adapter
        for request_id, (op, salt) in list(getattr(self, '_probe_pending', {}).items()):
            entry = adapter.retrieve_futures.get(request_id)
            if entry is None:
                raise RuntimeError(f'Retrieve disappeared before probe: {request_id}')
            future, _ = entry
            if future.query():
                if not future.result(timeout=60):
                    raise RuntimeError(f'Retrieve failed during integrity probe: {request_id}')
                self._snapshot('after_retrieve', request_id, op, salt)
                del self._probe_pending[request_id]
        return super().get_finished(finished_req_ids)
