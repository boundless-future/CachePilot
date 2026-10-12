"""Exact-wire END queue consumed only by native eager finished_sending."""
from scripts.owned_service_protocol import OwnedRequestClient


class ImmediateReceiptClient(OwnedRequestClient):
    def __init__(self, client, incarnation=None, record=None):
        super().__init__(client, incarnation)
        self.pending_ends = {}
        self.record = record or (lambda *args, **kwargs: None)

    def lookup(self, key, *args, **kwargs):
        if key.request_id in self.pending_ends:
            raise RuntimeError('Immediate raw ID reused before original STORE closure')
        return super().lookup(key, *args, **kwargs)

    def end_session(self, request_id):
        identity = self.identities.get(request_id)
        if identity is None:
            return
        previous = self.pending_ends.get(request_id)
        if previous is not None and previous != identity:
            raise RuntimeError('Immediate END overlaps raw identity generations')
        self.pending_ends[request_id] = identity
        self.record('immediate_end_deferred', request_id=request_id, wire_id=identity)

    def flush_finished(self, request_ids):
        for request_id in sorted(request_ids or ()):
            identity = self.pending_ends.get(request_id)
            if identity is None:
                continue
            # Native adapter already drained lookup/status RPCs before asking
            # for END. Do not remove our pending identity if submission fails.
            self.client.end_session(identity)
            self.pending_ends.pop(request_id)
            if self.identities.get(request_id) == identity:
                self.identities.pop(request_id)
            self.record('immediate_end_submitted', request_id=request_id, wire_id=identity)
