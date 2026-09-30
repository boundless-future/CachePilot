"""Allocator/lifecycle properties of the GPU-independent 3C reference model."""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from protection_state_machine import Block, FakeBlockPool, Phase, ProtectionMachine


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.blocks = tuple(Block(i, 1, f"prefix-{i}") for i in range(12))
        self.pool = FakeBlockPool(self.blocks, 16)
        self.machine = ProtectionMachine(self.pool, 8, 3, reserve_blocks=2)
        self.session = self.machine.arrive("r")

    def prepare(self, blocks=None, demand=2, start=0, tokens=1, session=None):
        return self.machine.prepare(session or self.session,
                                      self.blocks[:4] if blocks is None else blocks,
                                      start, demand, tokens)

    def assert_empty(self):
        self.machine.assert_invariants()
        self.assertFalse(self.pool.pins)
        self.assertFalse(self.machine.batches)
        self.assertCountEqual(self.pool.pin_events, self.pool.unpin_events)

    def test_zero_token_step_and_insufficient_headroom_fall_back_without_pins(self):
        self.assertEqual(self.prepare(tokens=0).reason, "zero_token_step")
        self.assertEqual(self.prepare(demand=14).reason, "no_safe_prefix")
        self.assert_empty()

    def test_budget_truncates_only_a_contiguous_prefix(self):
        decision = self.prepare(self.blocks, demand=10)
        self.assertEqual(decision.protected, 4)
        action = self.machine.submit(decision.token)
        self.assertEqual(action.blocks, self.blocks[:4])
        self.assertGreaterEqual(self.pool.allocatable, 12)
        self.machine.receipt(action.token, True, 4)
        self.assertEqual(self.machine.saved[self.session], 4)
        self.assert_empty()

    def test_hash_version_change_stops_prefix_and_blocks_cannot_change_while_pinned(self):
        stale = self.blocks[2]
        self.pool.overwrite(Block(stale.block_id, 2, "different"))
        decision = self.prepare()
        self.assertEqual(decision.protected, 2)
        with self.assertRaises(RuntimeError):
            self.pool.overwrite(Block(0, 2, "new"))
        self.machine.cancel(self.session)
        self.assert_empty()

    def test_prefix_gap_and_duplicate_physical_block_are_rejected(self):
        self.assertEqual(self.prepare(start=1).reason, "prefix_gap")
        with self.assertRaises(ValueError):
            self.prepare([self.blocks[0], self.blocks[0]])
        self.assert_empty()

    def test_partial_success_only_advances_saved_prefix(self):
        action = self.machine.submit(self.prepare().token)
        self.machine.receipt(action.token, True, 2)
        self.assertEqual(self.machine.saved[self.session], 2)
        decision = self.prepare(self.blocks[2:4], start=2)
        action = self.machine.submit(decision.token)
        self.machine.receipt(action.token, False)
        self.assertEqual(self.machine.saved[self.session], 2)
        self.assert_empty()

    def test_cancel_after_submit_waits_for_terminal_receipt(self):
        action = self.machine.submit(self.prepare().token)
        self.machine.cancel(self.session)
        self.assertEqual(len(self.pool.pins), 4)
        self.assertTrue(self.machine.batches[action.token].orphaned)
        self.machine.receipt(action.token, True, 4)
        self.assertNotIn(self.session, self.machine.saved)
        self.assertFalse(self.machine.receipt(action.token, True, 4))
        self.assert_empty()

    def test_reused_id_and_late_receipt_do_not_touch_new_generation(self):
        old = self.machine.submit(self.prepare().token)
        new = self.machine.arrive("r")
        new_action = self.machine.submit(self.prepare(session=new).token)
        self.assertEqual(len(self.pool.pins), 4)
        self.assertEqual(sum(self.pool.pins.values()), 8)
        self.machine.receipt(old.token, True, 4)
        self.assertEqual(self.machine.saved[new], 0)
        self.assertEqual(sum(self.pool.pins.values()), 4)
        self.machine.receipt(new_action.token, True, 4)
        self.assert_empty()

    def test_duplicate_dispatch_and_receipt_never_double_unpin(self):
        token = self.prepare().token
        self.assertEqual(self.prepare().reason, "session_inflight")
        self.assertIsNotNone(self.machine.submit(token))
        self.assertIsNone(self.machine.submit(token))
        self.assertTrue(self.machine.receipt(token, False))
        self.assertFalse(self.machine.receipt(token, False))
        self.assert_empty()

    def test_submission_rejection_releases_only_undispatched_work(self):
        token = self.prepare().token
        self.machine.submit_rejected(token)
        token = self.prepare().token
        self.machine.submit(token)
        with self.assertRaises(RuntimeError):
            self.machine.submit_rejected(token)
        self.machine.receipt(token, False)
        self.assert_empty()

    def test_invalid_receipt_does_not_release_live_work(self):
        token = self.prepare().token
        with self.assertRaises(RuntimeError):
            self.machine.receipt(token, True, 4)
        self.machine.submit(token)
        for success, count in [(False, 1), (True, 5), (True, -1), (True, True)]:
            with self.assertRaises(ValueError):
                self.machine.receipt(token, success, count)
        self.assertEqual(len(self.pool.pins), 4)
        self.machine.receipt(token, False)
        self.assert_empty()

    def test_inflight_budget_and_reset(self):
        actions = []
        for i in range(3):
            session = self.machine.arrive(str(i))
            actions.append(self.machine.submit(self.prepare(session=session).token))
        session = self.machine.arrive("fourth")
        self.assertEqual(self.prepare(session=session).reason, "inflight_budget")
        self.machine.reset()
        self.assertFalse(self.machine.current)
        self.assertTrue(self.machine.pending())
        for action in actions:
            self.machine.receipt(action.token, False)
        self.assert_empty()

    def test_pin_failure_rolls_back_prior_pins(self):
        original = self.pool.pin
        def pin(block):
            if block.block_id == 2:
                raise RuntimeError("allocation hook failed before pin")
            original(block)
        self.pool.pin = pin
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assert_empty()

    def test_allocator_pressure_cannot_overwrite_inflight_prefix(self):
        action = self.machine.submit(self.prepare(demand=10).token)
        allocated = self.pool.allocate(10)
        self.assertTrue(all(self.pool.matches(b) for b in action.blocks))
        self.assertEqual(self.pool.allocatable, 2)
        self.assertEqual(set(allocated), set(range(4, 14)))
        self.machine.cancel(self.session)
        self.assertTrue(self.machine.pending())
        self.machine.receipt(action.token, True, 4)
        self.pool.release_allocations(allocated)
        self.assert_empty()

    def test_underpredicted_demand_is_rejected_by_allocator(self):
        self.prepare(demand=2)
        with self.assertRaises(ValueError):
            self.pool.allocate(13)
        self.assertEqual(self.pool.allocatable, 12)
        self.machine.cancel(self.session)
        self.assert_empty()

    def test_request_id_wire_mode_blocks_reused_id_until_old_receipt(self):
        machine = ProtectionMachine(self.pool, 8, 3, 2, serialize_request_ids=True)
        old = machine.arrive("wire-id")
        decision = machine.prepare(old, self.blocks[:4], 0, 2, 1)
        machine.submit(decision.token)
        new = machine.arrive("wire-id")
        self.assertEqual(machine.prepare(new, self.blocks[:4], 0, 2, 1).reason,
                         "request_id_inflight")
        machine.receipt(decision.token, True, 4)
        self.assertEqual(machine.saved[new], 0)
        second = machine.prepare(new, self.blocks[:4], 0, 2, 1)
        self.assertIsNotNone(second.token)
        machine.cancel(new)
        machine.assert_invariants()
        self.assertFalse(self.pool.pins)

    def test_request_id_wire_mode_does_not_block_unrelated_requests(self):
        machine = ProtectionMachine(self.pool, 8, 3, 2, serialize_request_ids=True)
        one, two = machine.arrive("a"), machine.arrive("b")
        first = machine.prepare(one, self.blocks[:4], 0, 2, 1)
        machine.submit(first.token)
        second = machine.prepare(two, self.blocks[:4], 0, 2, 1)
        self.assertIsNotNone(second.token)
        machine.reset()
        machine.receipt(first.token, False)
        machine.assert_invariants()
        self.assertFalse(self.pool.pins)

    def test_seeded_event_interleavings_preserve_ownership(self):
        # Exercise new generations, shared chunks, cancel/reset, partial and
        # failed receipts, stale/double receipts. Check invariants every step.
        for seed in range(20):
            rng = random.Random(seed)
            pool = FakeBlockPool(self.blocks, 16)
            machine = ProtectionMachine(pool, 8, 3, 2)
            old_tokens = []
            for _ in range(300):
                op = rng.randrange(6)
                if op == 0 or not machine.current:
                    machine.arrive(str(rng.randrange(4)))
                elif op == 1:
                    session = rng.choice(list(machine.current.values()))
                    start = machine.saved[session]
                    decision = machine.prepare(session, self.blocks[start:], start,
                                               rng.randrange(17), rng.randrange(2))
                    if decision.token is not None:
                        if rng.randrange(3):
                            machine.submit(decision.token)
                        old_tokens.append(decision.token)
                elif op == 2:
                    machine.cancel(rng.choice(list(machine.current.values())))
                elif op == 3:
                    machine.reset()
                elif op == 4 and old_tokens:
                    token = rng.choice(old_tokens)
                    batch = machine.batches.get(token)
                    if batch and batch.phase is Phase.SUBMITTED:
                        success = bool(rng.randrange(2))
                        count = rng.randrange(len(batch.blocks) + 1) if success else 0
                        machine.receipt(token, success, count)
                    elif batch is None:
                        self.assertFalse(machine.receipt(token, False))
                elif op == 5:
                    for batch in list(machine.batches.values()):
                        if batch.phase is Phase.PREPARED:
                            machine.submit_rejected(batch.token)
                machine.assert_invariants()
            machine.reset()
            for token in list(machine.batches):
                machine.receipt(token, False)
            machine.assert_invariants()
            self.assertFalse(pool.pins)
            self.assertCountEqual(pool.pin_events, pool.unpin_events)


if __name__ == "__main__":
    unittest.main()
