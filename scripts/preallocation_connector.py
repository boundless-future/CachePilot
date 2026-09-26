"""Diagnostic-only preallocation observer; does not pin, store, or schedule."""
from vllm.v1.core.sched.scheduler import Scheduler

from scripts.allocation_diagnostic_connector import AllocationDiagnosticConnector
from scripts.preallocation_observer import install_scheduler_observer, lookup_snapshot


class PreallocationConnector(AllocationDiagnosticConnector):
    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        install_scheduler_observer(Scheduler)

    def observe_preallocation(self, snapshot):
        pool = self._kv_cache_manager.block_pool
        policy = self._lazy_offload_manager._policy
        for request in snapshot['requests']:
            if request['queue'] == 'waiting':
                request['lookup_state'], request['remote_hit_tokens'] = lookup_snapshot(
                    self.scheduler_adapter, request['request_id'])
        snapshot['historical_ema_blocks'] = policy._blocks_per_step_ema
        ranks = policy._free_queue_ranks(snapshot['free_blocks'])
        blocked = self._lazy_offload_manager._requests.in_flight_request_ids()
        pending = []
        for request_id, operations in policy._pending.items():
            for operation in operations:
                positions = [ranks[bid] for bid in operation.block_hashes if bid in ranks]
                pending.append(dict(**self._op(operation.store_metadata),
                                    nearest_free_rank=min(positions) if positions else None,
                                    hash_valid=all(pool.blocks[bid].block_hash == digest
                                                   for bid, digest in operation.block_hashes.items()),
                                    blocked=request_id in blocked))
        self._record('pre_step', **snapshot, pending=pending)

    def wait_for_save(self):
        metadata = self._get_connector_metadata()
        request_ids = [meta.request_id for meta in metadata.requests
                       if meta.direction == 'STORE']
        result = super().wait_for_save()
        if request_ids:
            self._record('store_submit', request_ids=request_ids)
        return result

    def build_connector_worker_meta(self):
        result = super().build_connector_worker_meta()
        if result is not None:
            self._record('store_worker_receipt',
                         completed=dict(result.completed_store_requests),
                         failed=sorted(result.failed_store_requests))
        return result

    def update_connector_output(self, connector_output):
        result = super().update_connector_output(connector_output)
        meta = connector_output.kv_connector_worker_meta
        if meta is not None:
            self._record('store_scheduler_receipt',
                         completed=dict(meta.completed_store_requests),
                         failed=sorted(meta.failed_store_requests))
        return result
