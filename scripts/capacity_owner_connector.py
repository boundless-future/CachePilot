"""Read-only scheduler ledger for the independent capacity-owner diagnostic."""
import hashlib
import json
import os
from pathlib import Path
import time

from scripts.protection_connector import ProtectionConnector


class CapacityOwnerConnector(ProtectionConnector):
    def _owner_record(self, event, **fields):
        path = Path(os.environ['CACHEPILOT_OWNED_SERVICE_DIR']).parent / 'scheduler-owner.jsonl'
        with path.open('a') as stream:
            stream.write(json.dumps(dict(event=event, unix_time=time.time(),
                monotonic_ns=time.monotonic_ns(), **fields)) + '\n')

    def _owner_snapshot(self, event):
        if self._handoff_role != 'SCHEDULER':
            return
        manager = self._lazy_offload_manager
        policy = manager._policy
        state = dict(finished=sorted(manager._requests.finished_request_ids()),
            inflight=sorted(manager._requests.in_flight_request_ids()),
            pending={rid: [dict(start=p.store_metadata.op.start,
                end=p.store_metadata.op.end, blocks=p.store_metadata.op.flat_block_ids)
                for p in ops] for rid, ops in policy._pending.items()},
            identities={url: dict(c.identities) for url, c in self.scheduler_adapter.req_clients.items()})
        if event == 'shutdown' or state != getattr(self, '_last_owner_state', None):
            self._owner_record(event, **state)
            self._last_owner_state = state

    def _get_or_create_request_tracker(self, request):
        result = super()._get_or_create_request_tracker(request)
        if not getattr(result, '_owner_observed', False):
            result._owner_observed = True
            self._owner_record('request_identity', request_id=request.request_id,
                prompt_token_sha256=hashlib.sha256(json.dumps(
                    request.prompt_token_ids, separators=(',', ':')).encode()).hexdigest())
        return result

    def build_connector_meta(self, scheduler_output):
        result = super().build_connector_meta(scheduler_output)
        self._owner_snapshot('scheduler_step')
        return result

    def update_connector_output(self, connector_output):
        result = super().update_connector_output(connector_output)
        self._owner_snapshot('worker_output')
        return result

    def request_finished(self, request, block_ids):
        result = super().request_finished(request, block_ids)
        self._owner_record('request_finished', request_id=request.request_id)
        self._owner_snapshot('finished_state')
        return result

    def shutdown(self):
        self._owner_snapshot('shutdown')
        return super().shutdown()
