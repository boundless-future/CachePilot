"""CPU transfer-plan checks for original ticket/range/group/buffer binding."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock

import test_leased_lookup_contract as lookup_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from transfer_plan_contract import KernelLayout, PlannedTransferHarness, RegisteredLayout
    from owned_lookup_contract import Worker
    from lmcache.v1.distributed.api import AttnWindowDesc
    from lmcache.v1.multiprocess.custom_types import IPCCacheServerKey
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    PlannedTransferHarness = None


@unittest.skipUnless(PlannedTransferHarness, "Needs pinned LMCache/native extension")
class TransferPlanTests(unittest.TestCase):
    key = lookup_fixture.LeasedLookupTests.key
    begin = lookup_fixture.LeasedLookupTests.begin
    bitmap = lookup_fixture.LeasedLookupTests.bitmap
    stage_load = lookup_fixture.LeasedLookupTests.stage_load
    complete_load = lookup_fixture.LeasedLookupTests.complete_load
    seed = lookup_fixture.LeasedLookupTests.seed
    ready = lookup_fixture.LeasedLookupTests.ready
    reset = lookup_fixture.LeasedLookupTests.reset
    assert_drained = lookup_fixture.LeasedLookupTests.assert_drained

    def setUp(self):
        lookup_fixture.LeasedLookupTests.setUp(self)
        self.transfers = PlannedTransferHarness(self.module)
        self.layout = RegisteredLayout(
            "registration-1", Worker("worker-0-0", 0), "test", 1,
            256, (64,), (KernelLayout(0, 128, 100),))
        self.transfers.register_layout(self.layout)

    def request_key(self, **changes):
        return replace(IPCCacheServerKey.from_token_ids(
            "test", 1, 0, list(range(768)), end=768, request_id="r"), **changes)

    def prepare(self, ticket, *, key=None, blocks=None, skip=0, registration=None):
        return self.transfers.prepare_plan(
            ticket, key if key is not None else self.request_key(),
            blocks if blocks is not None else [[0, 1, 2, 3, 4, 5]],
            registration=registration or self.layout.incarnation,
            skip_first_n_tokens=skip)

    def plan(self, **kwargs):
        keys, job, (ticket,) = self.ready(temporary=True)
        handle = self.prepare(ticket, **kwargs)
        self.assertIsNotNone(handle)
        return keys, job, ticket, handle

    def submit(self, handle):
        captured = {}
        def submit(plan, callback):
            captured.update(plan=plan, callback=callback)
            return True
        self.assertTrue(self.transfers.enqueue_plan(handle, submit))
        return captured

    def finish(self, captured):
        self.assertTrue(captured["callback"]())
        self.assertFalse(self.transfers.plans)
        self.assert_drained()

    def assert_offered(self, job, ticket):
        self.assertEqual(self.module.jobs[job.sequence].slots[ticket].state, "offered")
        self.assertFalse(self.module.accesses)
        self.assertFalse(self.l1.leases)

    def test_suffix_selects_original_buffers_and_holds_whole_shard(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        for i, key in enumerate(keys):
            self.l1._objects[key].memory_obj.data[:] = bytes([65 + i]) * 64
        originals = tuple(self.l1._objects[k].memory_obj for k in keys)
        handle = self.prepare(ticket, key=self.request_key(start=256), blocks=[[4, 5, 6, 7]])
        captured = self.submit(handle)
        plan = captured["plan"]
        self.assertEqual((plan.start, plan.end), (256, 768))
        self.assertEqual(plan.groups[0].keys, tuple(keys[1:]))
        self.assertTrue(all(b is original for b, original in
                            zip(plan.groups[0].buffers, originals[1:], strict=True)))
        self.assertEqual([bytes(b.data) for b in plan.groups[0].buffers],
                         [b"B" * 64, b"C" * 64])
        self.assertTrue(all(not self.l1.is_key_evictable(k) for k in keys))
        self.module.end_owned(job)
        self.assertFalse(self.allocator.freed)
        self.finish(captured)
        self.assertEqual(len(self.allocator.freed), 3)

    def test_block_aligned_apc_skip_and_metadata_are_frozen(self):
        blocks = [[10, 11, 12, 13, 14, 15]]
        keys, job, ticket, handle = self.plan(blocks=blocks, skip=128)
        blocks[0][0] = 99
        self.ctx.chunk_size = 999
        captured = self.submit(handle)
        plan = captured["plan"]
        self.assertEqual(plan.skip_first_n_tokens, 128)
        self.assertEqual(plan.block_ids, ((10, 11, 12, 13, 14, 15),))
        self.assertEqual(plan.layout.chunk_size, 256)
        self.finish(captured)

    def test_request_identity_is_checked_explicitly_not_ipc_equality(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        original = self.request_key()
        self.assertEqual(original, replace(original, request_id="different"))
        cases = dict(request_id="different", model_name="other", cache_salt="tenant",
                     world_size=2, worker_id=1, num_kv_readers=2,
                     token_ids=tuple(range(767)) + (999,))
        for field, value in cases.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare(ticket, key=replace(original, **{field: value}))
            self.assert_offered(job, ticket)
        self.module.end_owned(job)
        self.assert_drained()

    def test_range_validation_rejects_non_suffix_empty_unaligned_and_bool(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        for start, end in ((128, 768), (0, 512), (768, 768), (-256, 768),
                           (0, 1024), (True, 768), (0, 767)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.prepare(ticket, key=self.request_key(start=start, end=end))
            self.assert_offered(job, ticket)
        self.module.end_owned(job)
        self.assert_drained()

    def test_block_underflow_overflow_alias_types_and_group_counts(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        cases = ([[0, 1, 2]], [[0, 1, 2, 3, 4, 5, 6]], [[0, 1, 2, 3, 4, 100]],
                 [[0, 1, 2, 3, 4, -1]], [[0, 1, 2, 2, 4, 5]],
                 [[0, 1, 2, 3, 4, True]], [[0, 1, 2, 3, 4, "5"]],
                 [], [[0, 1, 2, 3, 4, 5], []], None)
        for blocks in cases:
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                self.transfers.prepare_plan(ticket, self.request_key(), blocks,
                                            registration=self.layout.incarnation)
            self.assert_offered(job, ticket)
        self.module.end_owned(job)
        self.assert_drained()

    def test_apc_skip_must_be_bounded_and_aligned_to_every_kernel(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        for skip in (-1, True, 1, 64, 768, 896):
            with self.subTest(skip=skip), self.assertRaises(ValueError):
                self.prepare(ticket, skip=skip)
            self.assert_offered(job, ticket)
        self.module.end_owned(job)
        self.assert_drained()

    def test_forged_ticket_and_missing_registration_do_not_claim_reader(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        for bad in (None, replace(ticket, worker=Worker("restart", 0)),
                    replace(ticket, lookup=replace(job, server="old-server"))):
            with self.subTest(ticket=bad), self.assertRaises(ValueError):
                self.prepare(bad)
        with self.assertRaises(ValueError):
            self.prepare(ticket, registration="old-registration")
        self.assert_offered(job, ticket)
        self.module.end_owned(job)
        self.assert_drained()

    def test_reregistration_before_enqueue_rejects_and_retires_original_lease(self):
        keys, job, ticket, handle = self.plan()
        self.transfers.register_layout(replace(self.layout, incarnation="new-registration"))
        submit = Mock()
        self.assertFalse(self.transfers.enqueue_plan(handle, submit))
        submit.assert_not_called()
        self.assertEqual(self.transfers.states[handle.sequence].phase, "closed")
        self.assertFalse(self.transfers.plans)
        self.assert_drained()

    def test_registration_change_after_submit_does_not_release_inflight_plan(self):
        keys, job, ticket, handle = self.plan()
        captured = self.submit(handle)
        self.transfers.register_layout(replace(self.layout, incarnation="new-registration"))
        self.module.end_owned(job)
        self.assertFalse(self.allocator.freed)
        self.assertIs(captured["plan"].layout, self.layout)
        self.finish(captured)

    def test_source_size_mismatch_rejects_before_submit_and_cleans_originals(self):
        self.transfers.register_layout(replace(self.layout, incarnation="wrong-size", object_bytes=(32,)))
        keys, job, ticket, handle = self.plan(registration="wrong-size")
        state = self.transfers.states[handle.sequence]
        self.assertEqual(state.phase, "closed")
        self.assertIn("size changed", state.errors[0])
        submit = Mock()
        self.assertFalse(self.transfers.enqueue_plan(handle, submit))
        submit.assert_not_called()
        self.assert_drained()

    def test_original_buffer_replacement_is_detected_at_submission(self):
        keys, job, ticket, handle = self.plan()
        old = self.l1._objects[keys[0]].memory_obj
        self.l1._objects[keys[0]].memory_obj = lookup_fixture.l1_fixture.CPUBuffer()
        submit = Mock()
        self.assertFalse(self.transfers.enqueue_plan(handle, submit))
        submit.assert_not_called()
        state = self.transfers.states[handle.sequence]
        self.assertIn("identity", state.errors[0])
        self.assertEqual(bytes(old.data), b"K" * 64)
        self.assert_drained()

    def test_two_objects_and_three_kernels_preserve_chunk_and_group_order(self):
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc(
            [-1, -1], group_kinds=("attention", "attention"))
        self.ctx.layout_desc_registry.find_group_layout_descs.return_value = {0: Mock(), 1: Mock()}
        layout = replace(self.layout, incarnation="two-objects", object_bytes=(64, 64),
                         kernels=(KernelLayout(0, 128, 100), KernelLayout(0, 64, 200),
                                  KernelLayout(1, 256, 300)))
        self.transfers.register_layout(layout)
        keys, job, (ticket,) = self.ready(temporary=True)
        handle = self.prepare(ticket, key=self.request_key(start=256),
                              blocks=[[0, 1, 2, 3], list(range(8)), [10, 11]],
                              registration=layout.incarnation)
        captured = self.submit(handle)
        plan = captured["plan"]
        self.assertEqual([g.kernel_ids for g in plan.groups], [(0, 1), (2,)])
        self.assertEqual([g.keys for g in plan.groups],
                         [tuple(keys[i] for i in (2, 4)), tuple(keys[i] for i in (3, 5))])
        self.assertEqual(len(self.transfers.states[handle.sequence].buffers), 6)
        self.finish(captured)
        self.assertEqual(len(self.allocator.freed), 6)

    def test_tp_shards_bind_encoded_rank_and_cleanup_independently(self):
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1], world_size=2)
        keys, job, tickets = self.ready(world_size=2, temporary=True)
        captures = []
        for ticket in tickets:
            layout = replace(self.layout, incarnation=f"reg-{ticket.worker.rank}",
                             worker=ticket.worker, world_size=2)
            self.transfers.register_layout(layout)
            handle = self.prepare(ticket, key=self.request_key(world_size=2, worker_id=ticket.worker.rank),
                                  registration=layout.incarnation)
            captures.append(self.submit(handle))
            encoded = self.module.jobs[job.sequence].ranks[ticket.worker.rank]
            self.assertEqual({k.kv_rank for k in captures[-1]["plan"].groups[0].keys}, {encoded})
        self.assertTrue(captures[0]["callback"]())
        self.assertEqual(len(self.allocator.freed), 3)
        self.assertTrue(captures[1]["callback"]())
        self.assertEqual(len(self.allocator.freed), 6)
        self.assert_drained()

    def test_unsupported_sliding_window_rejected_without_claim(self):
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([1])
        keys, job, (ticket,) = self.ready(temporary=True)
        with self.assertRaises(ValueError):
            self.prepare(ticket)
        self.assert_offered(job, ticket)
        self.module.end_owned(job)
        self.assert_drained()

    def test_invalid_trusted_layouts_and_reused_registration_rejected(self):
        layouts = (replace(self.layout, kernels=(KernelLayout(0, 100, 100),)),
                   replace(self.layout, kernels=(KernelLayout(1, 128, 100),)),
                   replace(self.layout, object_bytes=(64, 64)),
                   replace(self.layout, world_size=0), replace(self.layout, chunk_size=True),
                   replace(self.layout, kernels=(KernelLayout(0, 128, True),)),
                   replace(self.layout, object_bytes=[64]),
                   replace(self.layout, worker=Worker("", 0)),
                   replace(self.layout, worker=Worker(1, 0)))
        for layout in layouts:
            with self.subTest(layout=layout), self.assertRaises(ValueError):
                self.transfers.register_layout(layout)
        with self.assertRaises(ValueError):
            self.transfers.register_layout(self.layout)
        self.transfers.register_layout(replace(self.layout, incarnation="next-registration"))
        with self.assertRaises(ValueError):
            self.transfers.register_layout(self.layout)

    def test_end_before_enqueue_prevents_submission_and_releases_once(self):
        keys, job, ticket, handle = self.plan()
        self.module.end_owned(job)
        submit = Mock()
        self.assertFalse(self.transfers.enqueue_plan(handle, submit))
        submit.assert_not_called()
        self.assertEqual(len(self.allocator.freed), 3)
        self.assertFalse(self.transfers.stream_complete(handle))
        self.assert_drained()

    def test_partial_submit_exception_keeps_whole_shard_and_plan(self):
        keys, job, ticket, handle = self.plan(key=self.request_key(start=256), blocks=[[0, 1, 2, 3]])
        callbacks = []
        def partial(plan, callback):
            callbacks.append(callback)
            self.assertEqual(len(plan.groups[0].buffers), 2)
            raise RuntimeError("Copy submitted before later kernel failure")
        self.assertFalse(self.transfers.enqueue_plan(handle, partial))
        self.module.end_owned(job)
        self.reset(keys)
        self.l1.clear()
        self.assertFalse(self.allocator.freed)
        self.assertIn(handle, self.transfers.plans)
        self.assertTrue(all(not self.l1.is_key_evictable(k) for k in keys))
        self.assertTrue(callbacks[0]())
        self.assertFalse(callbacks[0]())
        self.assertFalse(self.transfers.plans)
        self.assert_drained()

    def test_concurrent_plan_preparation_claims_one_original_reader(self):
        keys, job, (ticket,) = self.ready(temporary=True)
        barrier = threading.Barrier(8)
        def prepare(_):
            barrier.wait()
            return self.prepare(ticket)
        with ThreadPoolExecutor(max_workers=8) as pool:
            handles = list(pool.map(prepare, range(8)))
        self.assertEqual(sum(h is not None for h in handles), 1)
        handle = next(h for h in handles if h is not None)
        self.finish(self.submit(handle))

    def test_callback_end_submit_return_race_without_held_submitter_locks(self):
        for _ in range(30):
            self.setUp()
            keys, job, ticket, handle = self.plan()
            barrier = threading.Barrier(3)
            callbacks = []
            def submit(plan, callback):
                callbacks.append(callback)
                barrier.wait(timeout=3)
                return True
            def complete():
                barrier.wait(timeout=3)
                return callbacks[0]()
            def end():
                barrier.wait(timeout=3)
                return self.module.end_owned(job)
            with ThreadPoolExecutor(max_workers=3) as pool:
                a = pool.submit(self.transfers.enqueue_plan, handle, submit)
                b = pool.submit(complete)
                c = pool.submit(end)
                self.assertTrue(a.result(timeout=5))
                self.assertTrue(b.result(timeout=5))
                self.assertTrue(c.result(timeout=5))
            self.assertEqual(len(self.allocator.freed), 3)
            self.assertFalse(self.transfers.plans)
            self.assert_drained()
