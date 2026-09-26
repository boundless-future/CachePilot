import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from preallocation_observer import demand_snapshot, install_scheduler_observer, lookup_snapshot
from analyze_preallocation import analyze


class FakeRequest:
    def __init__(self, request_id, tokens, computed=0, status='WAITING'):
        self.request_id = request_id
        self.num_tokens_with_spec = tokens
        self.num_computed_tokens = computed
        self.status = SimpleNamespace(name=status)


class ObserverTests(unittest.TestCase):
    def test_lookup_snapshot_reads_completed_future_without_polling_adapter(self):
        class Future:
            def query(self):
                return True

            def result(self, timeout):
                self_timeout.append(timeout)
                return 4

        self_timeout = []
        adapter = SimpleNamespace(_finished_lookup_results={}, _pending_lookups={'r'},
                                  _unacked_lookups={}, _per_server_hits={},
                                  _lookup_status={'r': {'server': (Future(), 0)}},
                                  _server_urls=['server'], lmcache_tokens_per_chunk=256)
        self.assertEqual(lookup_snapshot(adapter, 'r'), ('result_available', 1024))
        self.assertEqual(self_timeout, [0])
        self.assertEqual(adapter._per_server_hits, {})

    def test_demand_uses_slots_and_budget_without_mutating_queues(self):
        running = FakeRequest('r', 33, 32, 'RUNNING')
        waiting = [FakeRequest('a', 2048), FakeRequest('b', 2048)]
        FullAttentionManager = type('FullAttentionManager', (), {})
        manager = FullAttentionManager()
        manager.req_to_blocks = {'r': [object(), object()]}
        cache = SimpleNamespace(block_pool=SimpleNamespace(get_num_free_blocks=lambda: 100),
                                coordinator=SimpleNamespace(single_type_managers=[manager]))
        scheduler = SimpleNamespace(kv_cache_manager=cache, block_size=16,
                                    max_num_scheduled_tokens=256, max_num_running_reqs=2,
                                    num_waiting_for_streaming_input=0, running=[running],
                                    waiting=waiting, skipped_waiting=[], current_step=4)
        result = demand_snapshot(scheduler)
        self.assertEqual(result['upcoming_step'], 5)
        self.assertEqual(result['running_demand_blocks'], 1)
        self.assertEqual(result['waiting_demand_upper_blocks'], 128)
        self.assertEqual(len(waiting), 2)

    def test_wrapper_only_calls_opted_in_connector_once(self):
        class Scheduler:
            connector = SimpleNamespace(observe_preallocation=lambda row: seen.append(row))

            def schedule(self):
                return 'scheduled'

        seen = []
        install_scheduler_observer(Scheduler)
        install_scheduler_observer(Scheduler)
        # Snapshot is mocked here; full snapshot behavior is checked above.
        import preallocation_observer
        old = preallocation_observer.demand_snapshot
        preallocation_observer.demand_snapshot = lambda scheduler: {'step': 1}
        try:
            self.assertEqual(Scheduler().schedule(), 'scheduled')
        finally:
            preallocation_observer.demand_snapshot = old
        self.assertEqual(seen, [{'step': 1}])

    def test_analysis_requires_real_events_and_counts_false_warnings(self):
        with self.assertRaises(ValueError):
            analyze([])
        rows = [dict(event='pre_step', upcoming_step=1, monotonic_ns=0,
                     predicted_upper_blocks=128, running_demand_blocks=0,
                     block_size=16, requests=[], pending=[]),
                dict(event='pre_step', upcoming_step=2, monotonic_ns=100000000,
                     predicted_upper_blocks=128, running_demand_blocks=0,
                     block_size=16, requests=[], pending=[]),
                dict(event='allocation', upcoming_step=2, monotonic_ns=100100000,
                     allocated_blocks=128, affected=[])]
        result = analyze(rows)
        self.assertEqual(result['same_step_false_warnings'], 1)
        self.assertEqual(result['bursts'][0]['prior_warning_lead_ms'], 100)

    def test_same_step_risk_is_not_credited_as_early_warning(self):
        candidate = dict(request_id='old', start=0, end=256,
                         nearest_free_rank=1, hash_valid=True, blocked=False)
        rows = [dict(event='pre_step', upcoming_step=1, monotonic_ns=100,
                     predicted_upper_blocks=128, running_demand_blocks=0,
                     block_size=16, requests=[], pending=[candidate]),
                dict(event='allocation', upcoming_step=1, monotonic_ns=200,
                     allocated_blocks=128, affected=[dict(request_id='old', start=0,
                     end=256, intact_before_ids=[1])])]
        result = analyze(rows)
        self.assertEqual(result['affected_with_prior_online_risk'], 0)
        self.assertEqual(result['affected_with_same_step_only_risk'], 1)

    def test_lookup_forecast_and_worker_receipt_timing(self):
        request = dict(queue='waiting', status='WAITING', remaining_tokens=2048,
                       resident_blocks=0, lookup_state='result_available',
                       remote_hit_tokens=1024)
        rows = [dict(event='pre_step', upcoming_step=1, monotonic_ns=100,
                     predicted_upper_blocks=0, running_demand_blocks=0,
                     block_size=16, requests=[request], pending=[]),
                dict(event='allocation', upcoming_step=1, monotonic_ns=200,
                     allocated_blocks=128, affected=[]),
                dict(event='store_submit', monotonic_ns=300, request_ids=['r']),
                dict(event='store_worker_receipt', monotonic_ns=100000300,
                     completed={'r': 1}, failed=[])]
        result = analyze(rows, forecast='lookup_ready')
        self.assertEqual(result['warned_steps'], 0)
        self.assertEqual(analyze(rows, forecast='lookup_inflight')['warned_steps'], 0)
        self.assertEqual(result['store_timing']['worker_receipt_latency_ms']['p95'], 100)
        request['remote_hit_tokens'] = 2048
        self.assertEqual(analyze(rows, forecast='lookup_ready')['warned_steps'], 1)
        del request['lookup_state']
        with self.assertRaises(ValueError):
            analyze(rows, forecast='lookup_ready')


if __name__ == '__main__':
    unittest.main()
