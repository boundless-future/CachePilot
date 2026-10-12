"""Experimental single-worker zero-protection ledger connector.

Explicit class selection AND cachepilot.generation_ledger=true are required.
Not a production default. Source checks are inherited from the owned/idle stack.
"""
from types import SimpleNamespace

from scripts.idle_finalize_connector import IdleFinalizeConnector
from scripts.generation_rpc_client import GenerationRPCClient
from scripts.generation_session_ledger import GenerationSessionLedger
from scripts.generation_manager_bridge import GenerationManagerBridge
from scripts.owned_service_protocol import wire_id


class SchedulerLedgerClient(GenerationRPCClient):
    def __init__(self, client, incarnation, connector):
        super().__init__(client, incarnation, connector._generation_ledger)
        self.connector = connector

    @property
    def identities(self):
        # Used only for idle STORE identity preflight. QUERY routing remains
        # a separate map, never overwritten by a STORE-only generation.
        return self.ledger.current

    def __getattr__(self, name):
        if name in ('clear', 'report_block_allocation', 'close'):
            return getattr(self.client, name)
        raise AttributeError(name)

    def _lookup_route(self, rid):
        self.poll_rpc()
        return super()._lookup_route(rid)

    def end_session(self, rid):
        manager = self.connector._lazy_offload_manager
        if manager._policy.has_pending_request(rid) or manager._requests.has_in_flight(rid):
            raise RuntimeError('Raw END action precedes manager store closure')
        if rid in self.connector._generation_retrieves:
            # Cancellation while a retrieve is outstanding stays unresolved.
            self.connector._generation_end_requested.add(rid)
            return
        self.poll_rpc()
        for generation in self.ledger.generations.values():
            if generation.raw == rid and not generation.sealed:
                self.ledger.seal(generation.wire)
        self.connector._generation_end_requested.discard(rid)
        self.connector._flush_generation_ends()


class GenerationLedgerConnector(IdleFinalizeConnector):
    def __init__(self, config, role, kv_cache_config=None):
        extra = config.kv_transfer_config.kv_connector_extra_config
        if extra.get('cachepilot.generation_ledger') is not True:
            raise ValueError('Explicit generation ledger opt-in required')
        if extra.get('cachepilot.protection.max_chunks', 0) != 0:
            raise ValueError('Generation ledger pilot supports zero protection only')
        if (config.scheduler_config.async_scheduling or config.speculative_config is not None
                or config.lora_config is not None):
            raise ValueError('Generation ledger requires synchronous text-only scheduling')
        self._generation_ledger = GenerationSessionLedger()
        self._generation_retrieves = {}
        self._generation_end_requested = set()
        super().__init__(config, role, kv_cache_config)
        if role.name == 'SCHEDULER':
            if len(self.scheduler_adapter.req_clients) != 1:
                raise ValueError('Generation ledger supports one MP endpoint')
            self._lazy_offload_manager = GenerationManagerBridge(
                self._lazy_offload_manager, self._generation_ledger)
            self.scheduler_adapter.req_clients = {
                url: SchedulerLedgerClient(client.client, self._owned_client, self)
                for url, client in self.scheduler_adapter.req_clients.items()}

    def _flush_generation_ends(self):
        for client in self.scheduler_adapter.req_clients.values():
            client.poll_rpc()
            def send(identity):
                client.client.end_session(identity)
                self._handoff_record('generation_end_submitted', wire_id=identity)
            self._generation_ledger.submit_ends(send)
        unresolved = [g.wire for g in self._generation_ledger.generations.values()
                      if g.end_state == 'unresolved']
        if unresolved:
            raise RuntimeError(f'Generation END submission unresolved: {unresolved}')

    def generation_snapshot(self):
        ledger = self._generation_ledger
        return dict(generations=[dict(wire_id=g.wire, request_id=g.raw,
            sealed=g.sealed, end_state=g.end_state, error=g.error,
            operations=[dict(serial=t.serial, kind=t.kind, state=s)
                        for t, s in g.operations.items()])
            for g in ledger.generations.values()],
            pending_store=len(self._lazy_offload_manager.pending),
            inflight_store=len(self._lazy_offload_manager.inflight),
            retrieves=len(self._generation_retrieves),
            deferred_ends=sorted(self._generation_end_requested),
            pending_rpc=sum(len(c.pending_rpc) for c in
                            self.scheduler_adapter.req_clients.values()))

    def generation_needs_poll(self):
        return any(c.pending_rpc for c in self.scheduler_adapter.req_clients.values())

    def bind_kv_cache_manager(self, manager):
        super().bind_kv_cache_manager(manager)
        from scripts.generation_idle_pump import install_generation_idle_pump
        from vllm.v1.engine.core import EngineCoreProc
        install_generation_idle_pump(EngineCoreProc)

    def _handoff_record(self, event, **fields):
        if event == 'scheduler_built':
            # The inherited diagnostic emits before metadata is returned.
            # Record the same acquisition batch ID that will cross the wire.
            fields['batch_id'] = self._lazy_offload_manager.inflight[
                fields['request_id']][0]
        return super()._handoff_record(event, **fields)

    def shutdown(self):
        if self._handoff_role == 'SCHEDULER':
            self._flush_generation_ends()
            snapshot = self.generation_snapshot()
            self._handoff_record('generation_before_shutdown', **snapshot)
            if (snapshot['pending_rpc'] or snapshot['pending_store']
                    or snapshot['inflight_store'] or snapshot['retrieves']
                    or snapshot['deferred_ends'] or any(
                        g['end_state'] != 'submitted' for g in snapshot['generations'])):
                raise RuntimeError('Generation shutdown retains unresolved ownership')
        return super().shutdown()

    def _get_or_create_request_tracker(self, request):
        previous = self.request_trackers.get(request.request_id)
        tracker = super()._get_or_create_request_tracker(request)
        if previous is not tracker:
            if request.request_id in self._generation_retrieves:
                raise RuntimeError('Tracker replacement overlaps unresolved RETRIEVE')
            identity = wire_id(SimpleNamespace(request_id=request.request_id,
                                               request_configs=tracker.request_configs))
            self._generation_ledger.register(request.request_id, identity)
            self._handoff_record('generation_registered', request_id=request.request_id,
                                 wire_id=identity)
        return tracker

    def build_connector_meta(self, scheduler_output):
        self._flush_generation_ends()
        metadata = super().build_connector_meta(scheduler_output)
        # Reuse the already-validated scheduler/worker handoff format; replace
        # its serial with the batch identifier captured by the manager facade.
        for batch in metadata.cachepilot_handoffs_v1:
            rid = batch['request_id']
            identity, tickets = self._lazy_offload_manager.inflight[rid]
            if any(ticket.wire != batch['wire_id'] for ticket in tickets):
                raise RuntimeError('Handoff does not match acquired generation')
            batch['batch_id'] = identity
            self._handoffs.pending[rid]['batch_id'] = identity
        for item in metadata.requests:
            if item.direction == 'RETRIEVE':
                if item.request_id in self._generation_retrieves:
                    raise RuntimeError('Overlapping RETRIEVE generations')
                self._generation_retrieves[item.request_id] = self._generation_ledger.begin(
                    wire_id(item), 'retrieve')
        return metadata

    def update_connector_output(self, output):
        meta = output.kv_connector_worker_meta
        if meta is not None:
            from scripts.protection_connector import HandoffWorkerMetadata
            if not isinstance(meta, HandoffWorkerMetadata):
                raise ValueError('Generation ledger requires original receipt envelope')
            with self._lazy_offload_manager.receipt_scope(meta.batch_ids):
                result = super().update_connector_output(output)
        else:
            result = super().update_connector_output(output)
        for rid in output.finished_recving or ():
            ticket = self._generation_retrieves.pop(rid, None)
            if ticket is not None:
                self._generation_ledger.terminal(ticket)
                if rid in self._generation_end_requested:
                    for client in self.scheduler_adapter.req_clients.values():
                        client.end_session(rid)
        self._flush_generation_ends()
        return result

    def has_pending_push_work(self):
        if self._handoff_role == 'SCHEDULER':
            self._flush_generation_ends()
        return super().has_pending_push_work()
