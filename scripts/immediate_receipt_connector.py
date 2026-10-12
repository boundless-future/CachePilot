"""Opt-in synchronous TP1 immediate END-after-original-STORE candidate.

Native eager worker futures and vLLM block frees remain authoritative.
PREEMPTED, unhealthy-worker and overlapping-ID paths fail closed: they have
not passed this candidate's contracts. No lazy manager or protection budget.
"""
from scripts.owned_service_connector import OwnedServiceConnector
from scripts.immediate_end_queue import ImmediateReceiptClient


class ImmediateReceiptConnector(OwnedServiceConnector):
    def __init__(self, config, role, kv_cache_config=None):
        transfer = config.kv_transfer_config
        extra = transfer.kv_connector_extra_config
        if extra.get('cachepilot.immediate_receipt_end') is not True:
            raise ValueError('Explicit immediate receipt END opt-in required')
        if extra.get('lmcache.mp.lazy_offload') is not False or not transfer.is_kv_producer:
            raise ValueError('Immediate receipt END requires eager producer')
        if extra.get('cachepilot.protection.max_chunks', 0) != 0:
            raise ValueError('Immediate receipt END supports zero protection only')
        if (config.scheduler_config.async_scheduling or config.speculative_config is not None
                or config.lora_config is not None):
            raise ValueError('Immediate receipt END requires synchronous text-only scheduling')
        super().__init__(config, role, kv_cache_config)
        if role.name == 'SCHEDULER':
            if len(self.scheduler_adapter.req_clients) != 1:
                raise ValueError('Immediate receipt END supports one MP endpoint')
            self.scheduler_adapter.req_clients = {
                url: ImmediateReceiptClient(client.client, self._owned_client, self._record)
                for url, client in self.scheduler_adapter.req_clients.items()}

    def get_num_new_matched_tokens(self, request, num_computed_tokens):
        if request.status.name == 'PREEMPTED':
            raise RuntimeError('Immediate receipt END preemption is not validated')
        return super().get_num_new_matched_tokens(request, num_computed_tokens)

    def update_connector_output(self, connector_output):
        super().update_connector_output(connector_output)
        for client in self.scheduler_adapter.req_clients.values():
            client.flush_finished(connector_output.finished_sending)

    def get_finished(self, finished_req_ids):
        if not self.worker_adapter.is_healthy:
            raise RuntimeError('Unhealthy worker cannot prove original STORE terminal')
        return super().get_finished(finished_req_ids)
