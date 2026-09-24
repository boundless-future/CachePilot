"""Read-only decision ledger for the pinned LMCache EVICTION_AWARE policy.

Tracing changes timing: use this to explain mechanisms, not report latency gains.
Logs retain request IDs and prefix digests, not prompt text or token IDs.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector


def prefix_digest(tokens, end):
    return hashlib.sha256(json.dumps(list(tokens[:end]), separators=(',', ':')).encode()).hexdigest()


class DecisionConnector(LMCacheMPConnector):
    def _record(self, event, **data):
        if not hasattr(self, '_decision_file'):
            path = Path(os.environ.get('CACHEPILOT_DECISION_DIR', '/tmp/cachepilot-decision'))
            path.mkdir(parents=True, exist_ok=True)
            self._decision_file = path / f'scheduler-{os.getpid()}.jsonl'
        row = dict(event=event, step=getattr(self,'_decision_step',0),
                   monotonic_ns=time.monotonic_ns(), **data)
        with self._decision_file.open('a') as f:
            f.write(json.dumps(row)+'\n')

    def _op(self, meta):
        op = meta.op
        return dict(request_id=meta.request_id, start=op.start, end=op.end,
                    prefix_sha256=prefix_digest(op.token_ids,op.end),
                    block_ids=op.flat_block_ids)

    def on_new_request(self, request):
        ids = request.prompt_token_ids
        if ids:
            self._record('request', request_id=request.request_id, prompt_tokens=len(ids),
                         prefixes={str(n):prefix_digest(ids,n) for n in range(256,len(ids)+1,256)})
        return super().on_new_request(request)

    def get_num_new_matched_tokens(self, request, num_computed_tokens):
        value = super().get_num_new_matched_tokens(request, num_computed_tokens)
        if value[0] is not None:
            self._record('lookup',request_id=request.request_id,
                         gpu_computed_tokens=num_computed_tokens,external_tokens=value[0],async_load=value[1])
        return value

    def bind_gpu_block_pool(self, gpu_block_pool):
        super().bind_gpu_block_pool(gpu_block_pool)
        if not self.lazy_offload or getattr(self,'_decision_bound',False):return
        self._decision_bound = True
        policy = self._lazy_offload_manager._policy
        if policy.__class__.__name__ != 'EvictionAwareStoreQueue':
            raise RuntimeError(f'Unsupported traced policy: {type(policy).__name__}')
        self._record('config',config=asdict(policy._config),gpu_blocks=gpu_block_pool.num_gpu_blocks)
        original_add, original_drain = policy.add, policy.drain
        original_drop = policy._drop_evicted_suffix

        def add(meta, hashes):
            before = policy._counters.admitted
            result = original_add(meta,hashes)
            self._record('admission',accepted=policy._counters.admitted>before,**self._op(meta))
            return result

        def drop(request_id, ops):
            survivors = original_drop(request_id,ops)
            for op in ops[len(survivors):]:
                changed = [bid for bid,h in op.block_hashes.items() if gpu_block_pool.blocks[bid].block_hash!=h]
                self._record('dropped_evicted',**self._op(op.store_metadata),
                             changed_block_ids=changed,age_seconds=policy._now-op.admitted_at_time)
            return survivors

        def drain(signals):
            before = asdict(policy._counters)
            result = original_drain(signals)
            ranks = policy._free_queue_ranks(gpu_block_pool.get_num_free_blocks())
            pending = []
            for rid,ops in policy._pending.items():
                for op in ops:
                    positions = [ranks[b] for b in op.block_hashes if b in ranks]
                    pending.append(dict(**self._op(op.store_metadata),
                        nearest_free_rank=min(positions) if positions else None,
                        blocked=rid in signals.blocked_request_ids))
            self._record('drain',new_blocks=signals.new_blocks_allocated,
                         next_step_estimate=signals.est_next_step_blocks,
                         allocation_ema=policy._blocks_per_step_ema,danger_depth=policy._danger_depth(),
                         free_blocks=gpu_block_pool.get_num_free_blocks(),pending=pending,
                         counter_delta={k:v-before[k] for k,v in asdict(policy._counters).items()})
            for item in result.items:
                for meta,_ in item.metadatas:self._record('emitted',**self._op(meta))
            return result

        policy.add, policy.drain, policy._drop_evicted_suffix = add, drain, drop

    def build_connector_meta(self, scheduler_output):
        self._decision_step = getattr(self,'_decision_step',0)+1
        per_request = getattr(scheduler_output, 'num_scheduled_tokens', None)
        self._record('scheduled',tokens=scheduler_output.total_num_scheduled_tokens,
                     per_request=None if per_request is None else dict(per_request))
        result = super().build_connector_meta(scheduler_output)
        for meta in result.requests:
            self._record('submitted_'+meta.direction.lower(),**self._op(meta))
        return result
