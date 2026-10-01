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
    def update_state_after_alloc(self, request, blocks, num_external_tokens):
        if os.environ.get('CACHEPILOT_DECODE_PROBE') == '1':
            if not hasattr(self, '_decode_prompt_lengths'):
                self._decode_prompt_lengths = {}
            self._decode_prompt_lengths[request.request_id] = len(request.prompt_token_ids)
        return super().update_state_after_alloc(request, blocks, num_external_tokens)

    def build_connector_meta(self, scheduler_output):
        metadata = super().build_connector_meta(scheduler_output)
        if os.environ.get('CACHEPILOT_DECODE_PROBE') != '1':
            return metadata
        computed = {r.req_id: r.num_computed_tokens for r in scheduler_output.scheduled_new_reqs}
        cached = scheduler_output.scheduled_cached_reqs
        computed.update(zip(cached.req_ids, cached.num_computed_tokens, strict=True))
        snapshots = []
        for request_id, count in scheduler_output.num_scheduled_tokens.items():
            prompt_length = self._decode_prompt_lengths[request_id]
            begin = computed[request_id]
            if begin < prompt_length:
                continue
            # This diagnostic intentionally supports serial, non-speculative decode.
            if count != 1:
                raise RuntimeError('Decode probe requires one scheduled token per request')
            tracker = self.request_trackers[request_id]
            ids = tracker.get_token_ids()
            if begin >= len(ids) or set(tracker.allocated_block_ids) != {0}:
                raise RuntimeError(f'Decode ledger unavailable: end={begin + 1} ledger={len(ids)} '
                                   f'groups={list(tracker.allocated_block_ids)}; use --no-async-scheduling')
            snapshots.append(dict(request_id=request_id, end=begin + 1,
                decode_index=begin - prompt_length + 1, salt=tracker.cache_salt,
                token_ids=ids[:begin + 1], block_ids=list(tracker.allocated_block_ids[0])))
        metadata.cachepilot_decode_snapshots = snapshots
        for request_id in scheduler_output.finished_req_ids:
            self._decode_prompt_lengths.pop(request_id, None)
        return metadata

    def register_kv_caches(self, kv_caches):
        super().register_kv_caches(kv_caches)
        self._probe_refs = {}
        self._probe_pending = {}
        self._decode_refs = {}
        self._probe_dir = Path(os.environ['CACHEPILOT_PROBE_DIR'])
        self._probe_dir.mkdir(parents=True, exist_ok=True)
        adapter = self.worker_adapter
        self._record(dict(phase='worker_layout', chunk_size=adapter.lmcache_tokens_per_chunk,
            groups=[dict(tokens_per_block=g.tokens_per_block, recurrent_state=g.recurrent_state)
                    for g in adapter.engine_group_infos],
            tensors={name: dict(shape=list(t.shape), stride=list(t.stride()), dtype=str(t.dtype),
                                device=str(t.device), pointer=t.data_ptr(), element_bytes=t.element_size())
                     for name,t in adapter.kv_caches.items()}))
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

    def wait_for_save(self):
        if os.environ.get('CACHEPILOT_DECODE_PROBE') == '1':
            metadata = self._get_connector_metadata()
            for row in getattr(metadata, 'cachepilot_decode_snapshots', []):
                self._snapshot_decode(row)
        return super().wait_for_save()

    def _snapshot_decode(self, snapshot):
        adapter = self.worker_adapter
        groups = adapter.engine_group_infos
        if len(groups) != 1 or groups[0].recurrent_state:
            raise RuntimeError('Decode probe requires one dense attention group')
        span = groups[0].tokens_per_block
        position = snapshot['end'] - 1
        block = snapshot['block_ids'][position // span]
        slot = position % span
        key = token_key(snapshot['token_ids'], snapshot['salt'], snapshot['end'])
        row = {k: v for k, v in snapshot.items() if k not in ('token_ids', 'block_ids')}
        row.update(phase='decode', token_prefix_sha256=key, block_id=block, slot=slot, layers=[])
        torch.cuda.synchronize()
        for name, tensor in adapter.kv_caches.items():
            # Verified Qwen3-4B worker layout: [block, heads, token, head_dim].
            # Read only the computed token, never uninitialized block padding.
            if tensor.ndim != 4 or tensor.shape[2] != span:
                raise RuntimeError(f'Unsupported decode KV layout: {tensor.shape}')
            value = tensor[block, :, slot, :].contiguous().cpu()
            ref_key = (key, name)
            reference = self._decode_refs.get(ref_key)
            item = dict(layer=name, shape=list(value.shape), dtype=str(value.dtype),
                sha256=hashlib.sha256(value.view(torch.uint8).numpy().tobytes()).hexdigest(),
                reference_present=reference is not None)
            if reference is None:
                if len(self._decode_refs) >= 16384:
                    raise RuntimeError('Decode reference budget exceeded')
                self._decode_refs[ref_key] = value
            else:
                item['bitwise_equal'] = torch.equal(value.view(torch.uint8), reference.view(torch.uint8))
                item['max_abs_difference'] = float((value.float() - reference.float()).abs().max())
                item['different_elements'] = int((value != reference).sum())
            row['layers'].append(item)
        self._record(row)

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
