"""Serial scheduler quiescence policy; never frees GPU blocks or submits STORE."""
from cachepilot.integration.lmcache.protocol import wire_id
import queue


def finalize_idle_pending(connector, scheduler):
    # Paused/streaming/remote-retrieve/deferred-free requests are not idle.
    if any((scheduler.requests, scheduler.running, scheduler.waiting,
            scheduler.skipped_waiting, scheduler.deferred_frees)):
        return 0
    manager = connector._lazy_offload_manager
    if manager._requests.in_flight_request_ids() or connector._handoffs.pending:
        return 0
    bounded = connector._bounded
    if bounded is not None and (bounded.prepared or bounded.inflight):
        return 0
    policy = manager._policy
    pending = list(policy._pending.items())
    # Bounded to the service ownership admission limit; no engine wake/spin.
    if len(pending) > 256:
        raise RuntimeError('Idle pending cardinality exceeds supported limit')
    plans = []
    for rid, ops in pending:
        if not ops or not manager._requests.is_finished(rid):
            raise RuntimeError('Idle candidate is not a finished pending request')
        identities = {wire_id(p.store_metadata) for p in ops}
        if len(identities) != 1:
            raise RuntimeError('Idle pending generation mismatch')
        identity, = identities
        if not connector.scheduler_adapter.req_clients or any(
                c.identities.get(rid) != identity
                for c in connector.scheduler_adapter.req_clients.values()):
            raise RuntimeError('Idle pending differs from current wire identity')
        plans.append((rid, identity, [(p.store_metadata.op.start,
                                     p.store_metadata.op.end) for p in ops]))
    # All candidates validated before mutating any policy entry.
    total = 0
    for rid, identity, ranges in plans:
        connector._handoff_record('idle_pending_drop_planned', request_id=rid,
            wire_id=identity, ranges=ranges, reason='scheduler_quiescent')
        count = policy.drop_request(rid)
        manager._release_session(rid)
        connector.scheduler_adapter.end_session(rid)
        connector._handoff_record('idle_pending_dropped', request_id=rid,
            wire_id=identity, ranges=ranges, dropped=count,
            reason='scheduler_quiescent', acquired_pins=0, submitted_stores=0)
        total += count
    return total


def install_idle_scheduler(scheduler_type):
    if getattr(scheduler_type.has_requests, '_cachepilot_idle_finalize', False):
        return
    original = scheduler_type.has_requests

    def has_requests(scheduler):
        busy = original(scheduler)
        connector = scheduler.connector
        if (not busy and getattr(connector, '_idle_finalize', False)
                and not getattr(connector, '_idle_grace_seconds', 0)):
            finalize_idle_pending(connector, scheduler)
        return busy
    has_requests._cachepilot_idle_finalize = True
    scheduler_type.has_requests = has_requests


class IdleInputQueue:
    """Only the existing owner thread finalizes; producer operations delegate."""
    def __init__(self, original, core):
        self.original, self.core = original, core

    def __getattr__(self, name):
        return getattr(self.original, name)

    def get(self, block=True, timeout=None):
        c = self.core.scheduler.connector
        grace = getattr(c, '_idle_grace_seconds', 0)
        pending = getattr(getattr(c, '_lazy_offload_manager', None), '_policy', None)
        eligible = (block and timeout is None and grace > 0
                    and getattr(c, '_idle_finalize', False)
                    and pending is not None and bool(pending._pending))
        if not eligible:
            return self.original.get(block=block, timeout=timeout)
        try:
            # A queued arrival always wins, cancels this idle interval, and
            # reaches the normal core handler unchanged.
            return self.original.get(block=True, timeout=grace)
        except queue.Empty:
            if self.core.is_running() and not self.core.has_work() and self.original.empty():
                finalize_idle_pending(c, self.core.scheduler)
            # Native _process_input_queue handles Empty and rechecks shutdown.
            raise


def install_idle_core(core_type):
    if getattr(core_type._process_input_queue, '_cachepilot_idle_finalize', False):
        return
    original = core_type._process_input_queue

    def process(core):
        c = core.scheduler.connector
        if (getattr(c, '_idle_finalize', False)
                and getattr(c, '_idle_grace_seconds', 0) > 0
                and not isinstance(core.input_queue, IdleInputQueue)):
            core.input_queue = IdleInputQueue(core.input_queue, core)
        return original(core)
    process._cachepilot_idle_finalize = True
    core_type._process_input_queue = process
