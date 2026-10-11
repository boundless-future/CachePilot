"""Diagnostic identity timeline; no changes to lookup, END or idle decisions."""
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

from scripts.idle_identity_probe import IdleIdentityProbeConnector
from scripts.idle_identity_observation import snapshot
from scripts.owned_service_protocol import wire_id


from scripts.identity_timeline_observation import observe_call


class IdentityTimelineConnector(IdleIdentityProbeConnector):
    def _timeline(self, event, **fields):
        # A failed diagnostic write must be visible: this is not a performance
        # or repair candidate, and incomplete traces cannot establish ordering.
        directory = Path(os.environ['CACHEPILOT_IDLE_IDENTITY_DIR'])
        directory.mkdir(parents=True, exist_ok=True)
        row = dict(event=event, role=self._handoff_role, pid=os.getpid(),
                   monotonic_ns=time.monotonic_ns(), **fields)
        with (directory / ('timeline-' + self._handoff_role + '.jsonl')).open(
                'a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')

    def _state(self):
        return dict(pending=snapshot(self), mappings={url: dict(c.identities)
            for url, c in self.scheduler_adapter.req_clients.items()},
            inflight=sorted(self._lazy_offload_manager._requests.in_flight_request_ids()))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._handoff_role == 'SCHEDULER':
            for url, client in self.scheduler_adapter.req_clients.items():
                for name in ('lookup', 'end_session'):
                    def describe(args, kwargs, client=client, url=url):
                        key = args[0]
                        rid = key if isinstance(key, str) else key.request_id
                        return dict(url=url, request_id=rid,
                            wire_id=None if isinstance(key, str) else wire_id(key),
                            current=client.identities.get(rid), **self._state())
                    observe_call(client, name, self._timeline, describe)
            manager = self._lazy_offload_manager
            for name in ('on_request_arrived', 'on_request_reset',
                         'on_request_finished', 'add_store_candidate', '_release_session'):
                def describe(args, kwargs):
                    key = args[0]
                    rid = key if isinstance(key, str) else key.request_id
                    return dict(request_id=rid,
                        wire_id=None if isinstance(key, str) else wire_id(key),
                        **self._state())
                observe_call(manager, name, self._timeline, describe)
        else:
            for name in ('store', 'retrieve'):
                observe_call(self.worker_adapter.req_client, name, self._timeline,
                    lambda args, kwargs: dict(request_id=args[0].request_id,
                        wire_id=wire_id(args[0])))

    def _get_or_create_request_tracker(self, request):
        previous = self.request_trackers.get(request.request_id)
        tracker = super()._get_or_create_request_tracker(request)
        if tracker is not previous:
            key = SimpleNamespace(request_id=request.request_id,
                                  request_configs=tracker.request_configs)
            self._timeline('tracker_created', request_id=request.request_id,
                wire_id=wire_id(key), replaced=previous is not None,
                request_status=request.status.name, **self._state())
        return tracker

    def shutdown(self):
        if self._handoff_role == 'SCHEDULER':
            self._timeline('before_shutdown', **self._state())
        return super().shutdown()
