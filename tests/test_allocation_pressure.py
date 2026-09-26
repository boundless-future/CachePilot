import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from allocation_pressure import install_allocation_pressure


@dataclass(frozen=True)
class Signals:
    new_blocks_allocated: int
    est_next_step_blocks: int = 1
    blocked_request_ids: frozenset = frozenset({'in-flight'})


class Pool:
    def get_new_blocks(self, num_blocks):
        if num_blocks < 0:
            raise ValueError('allocation failed')
        self.result = [object() for _ in range(num_blocks)]
        return self.result


class AllocationTests(unittest.TestCase):
    def make(self):
        seen = []
        pool = Pool()
        policy = SimpleNamespace(drain=lambda signals: seen.append(signals) or signals)
        counter = install_allocation_pressure(pool, policy)
        return pool, policy, counter, seen

    def test_async_allocation_and_resume_not_double_counted(self):
        pool, policy, counter, seen = self.make()
        result = pool.get_new_blocks(128)
        self.assertIs(result, pool.result)
        counter.begin_step()
        original = Signals(0)
        policy.drain(original)
        counter.begin_step()
        policy.drain(Signals(128))  # Request resumes with old block IDs.
        self.assertEqual([s.new_blocks_allocated for s in seen], [128, 0])
        self.assertEqual(original.new_blocks_allocated, 0)
        self.assertEqual(seen[0].blocked_request_ids, original.blocked_request_ids)
        self.assertEqual(seen[0].est_next_step_blocks, 1)
        self.assertEqual(counter.total_allocated, counter.total_consumed)

    def test_zero_token_steps_carry_once_until_existing_drain(self):
        pool, policy, counter, seen = self.make()
        pool.get_new_blocks(128)
        counter.begin_step()  # No upstream drain on this step.
        counter.begin_step()  # Another idle step does not erase pending pressure.
        pool.get_new_blocks(6)
        counter.begin_step()
        self.assertEqual(counter.last_step, 6)
        policy.drain(Signals(134))
        counter.begin_step()
        policy.drain(Signals(0))
        self.assertEqual([s.new_blocks_allocated for s in seen], [134, 0])
        self.assertEqual(counter.pending, 0)

    def test_failed_allocation_and_rebinding(self):
        pool, policy, counter, seen = self.make()
        original = pool.get_new_blocks
        self.assertIs(install_allocation_pressure(pool, policy), counter)
        self.assertIs(pool.get_new_blocks, original)
        with self.assertRaises(ValueError):
            pool.get_new_blocks(-1)
        with self.assertRaises(ValueError):
            install_allocation_pressure(Pool(), policy)
        pool.get_new_blocks(2)
        counter.begin_step()
        policy.drain(Signals(99))
        self.assertEqual(counter.total_allocated, 2)
        self.assertEqual(seen[0].new_blocks_allocated, 2)

    def test_upstream_exception_propagates_without_retrying_drain(self):
        pool = Pool()
        calls = []

        def fail(signals):
            calls.append(signals)
            raise RuntimeError('upstream failed')

        policy = SimpleNamespace(drain=fail)
        counter = install_allocation_pressure(pool, policy)
        pool.get_new_blocks(3)
        counter.begin_step()
        with self.assertRaisesRegex(RuntimeError, 'upstream failed'):
            policy.drain(Signals(0))
        self.assertEqual(len(calls), 1)
        self.assertEqual(counter.pending, 0)


if __name__ == '__main__':
    unittest.main()
