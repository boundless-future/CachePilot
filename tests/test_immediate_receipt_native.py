"""Pinned native connector callbacks; no simulated native terminal claim."""
from types import SimpleNamespace as NS
import pytest
pytest.importorskip('lmcache')
from vllm import SamplingParams
from vllm.v1.request import Request, RequestStatus
from scripts.immediate_receipt_connector import ImmediateReceiptConnector
from scripts.immediate_end_queue import ImmediateReceiptClient


def test_native_finish_queues_end_until_worker_finished_sending():
    connector = object.__new__(ImmediateReceiptConnector)
    connector.lazy_offload = False
    connector._can_store = True
    connector.request_trackers = {}
    calls = []
    client = ImmediateReceiptClient(NS(end_session=lambda wire: calls.append(wire)))
    client.identities['raw'] = 'wire'
    connector.scheduler_adapter = NS(req_clients={'url': client},
        cleanup_lookup_result=lambda rid: None, end_session=client.end_session)
    request = Request('raw', list(range(512)), SamplingParams(max_tokens=192), None)
    assert connector.request_finished(request, [1, 2])[0] is True
    assert calls == [] and client.pending_ends == {'raw': 'wire'}
    connector.update_connector_output(NS(kv_cache_events=None, finished_sending=None))
    assert calls == []
    connector.update_connector_output(NS(kv_cache_events=None, finished_sending={'raw'}))
    assert calls == ['wire'] and client.pending_ends == {}


def test_unhealthy_worker_and_preemption_fail_closed():
    connector = object.__new__(ImmediateReceiptConnector)
    connector.worker_adapter = NS(is_healthy=False)
    with pytest.raises(RuntimeError, match='original STORE terminal'):
        connector.get_finished({'raw'})
    request = Request('raw', [1], SamplingParams(max_tokens=1), None)
    request.status = RequestStatus.PREEMPTED
    with pytest.raises(RuntimeError, match='preemption'):
        connector.get_num_new_matched_tokens(request, 0)


@pytest.mark.parametrize('success', [True, False])
def test_real_worker_future_blocks_finished_sending_until_original_result(success):
    import threading
    from lmcache.integration.vllm.vllm_multi_process_adapter import LMCacheMPWorkerAdapter
    from lmcache.v1.multiprocess.futures import MessagingFuture
    worker = object.__new__(LMCacheMPWorkerAdapter)
    worker.dispatcher = None
    worker._health_event = threading.Event()
    worker._health_event.set()
    future = MessagingFuture()
    worker.store_futures = {'raw': future}
    worker.retrieve_futures = {}
    worker.store_events = {'raw': object()}
    worker.retrieve_events = {}
    worker._pending_store_kv_events = {}
    worker._dropped_retrieves = set()
    worker.finished_stores = set()
    worker.previously_finished = set()
    worker._returned_finished = set()
    worker.request_telemetry = NS(on_request_store_finished=lambda **kw: None)
    worker.parallel_strategy = NS(kv_world_size=1, kv_worker_id=0)
    worker.model_name = 'contract'
    worker._publish_store_kv_events = lambda rid: None
    assert worker.get_finished({'raw'}) == (set(), set())
    assert worker.store_futures['raw'] is future
    future.set_result(success)
    assert worker.get_finished(set()) == ({'raw'}, set())
    assert worker.store_futures == worker.store_events == {}
    assert worker.get_finished({'raw'}) == (set(), set())


def test_native_no_store_finished_request_reports_once():
    from lmcache.integration.vllm.vllm_multi_process_adapter import LMCacheMPWorkerAdapter
    worker = object.__new__(LMCacheMPWorkerAdapter)
    worker.finished_stores = set()
    worker.previously_finished = set()
    worker._returned_finished = set()
    worker.store_futures = {}
    assert worker._process_finished_stores(set(), {'raw'}) == {'raw'}
    assert worker._process_finished_stores(set(), {'raw'}) == set()
