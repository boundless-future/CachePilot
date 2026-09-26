import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from preallocation_observer import demand_snapshot, install_scheduler_observer
from analyze_preallocation import analyze


class FakeRequest:
    def __init__(self, request_id, tokens, computed=0, status='WAITING'):
        self.request_id = request_id
        self.num_tokens_with_spec = tokens
        self.num_computed_tokens = computed
        self.status = SimpleNamespace(name=status)


class ObserverTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
