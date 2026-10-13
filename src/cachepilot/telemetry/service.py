"""Independent, read-only exact-wire observer on the existing owned service.

No END, cleanup, admission, or transfer behavior changes. A submission at the
scheduler is never enough: server-side END handler return and final snapshots
must independently establish session removal and ownership drain.
"""
import functools
import os
import sys


def install_observer(service):
    from lmcache.v1.multiprocess.modules.lookup import LookupModule
    original_end = LookupModule.end_session
    original_status = LookupModule.report_status

    @functools.wraps(original_end)
    def end(self, request_id):
        with service.gate:
            existed = self._ctx.session_manager.get(request_id) is not None
            result = original_end(self, request_id)
            remains = self._ctx.session_manager.get(request_id) is not None
            service.record('generation_server_end_returned', request_id,
                session_existed=existed, session_remains=remains,
                owned_job_remains=request_id in service.jobs)
            return result

    @functools.wraps(original_status)
    def status(self):
        with service.gate:
            result = original_status(self)
            manager = self._ctx.session_manager
            with manager._lock:
                sessions = sorted(manager._sessions)
            result['generation_observer'] = dict(session_ids=sessions,
                owned_job_ids=sorted(service.jobs),
                transfer_wire_ids=sorted(t['owner'].request_id
                                        for t in service.transfers.values()))
            return result

    LookupModule.end_session = end
    LookupModule.report_status = status
