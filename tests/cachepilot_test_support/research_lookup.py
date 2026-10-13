"""Real LookupModule folding with original tokens and simulated worker acks."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
try:
    from owned_lookup_contract import OwnedLookupHarness, Worker, ReaderTicket
    from owned_storage_contract import OwnedStorageHarness
    from owned_prefetch_contract import OwnedPrefetchHarness, JobHandle, token_id
    from reservation_l1_contract import ReservationL1Harness
    from lmcache.lmcache_native import Bitmap
    from lmcache.v1.distributed.api import AttnWindowDesc, ObjectKey
    from lmcache.v1.distributed.error import L1Error
    from lmcache.v1.multiprocess.custom_types import IPCCacheServerKey
    from lmcache.v1.distributed.storage_controllers.prefetch_controller import PrefetchPhase
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    OwnedLookupHarness = None


class OwnedLookupSetup:
    def setUp(self):
        self.allocator = Mock()
        self.allocator.allocate.side_effect = lambda layout, count: (
            L1Error.SUCCESS, [Mock(name=f"buffer-{i}") for i in range(count)])
        self.l1 = ReservationL1Harness(self.allocator, Mock())
        self.adapter = Mock()
        self.controller = OwnedPrefetchHarness(self.l1, Mock(), adapters={0: self.adapter},
            descriptors={0: SimpleNamespace(type_name="CPU-fixture")})
        self.storage = OwnedStorageHarness(self.l1, self.controller, Mock())
        self.ctx = Mock()
        self.ctx.chunk_size = 256
        self.ctx.event_bus.has_subscribers.return_value = False
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1])
        self.ctx.layout_desc_registry.find_group_layout_descs.return_value = {0: Mock()}
        self.hashes = [b'a', b'b', b'c']
        self.ctx.token_hasher.compute_chunk_hashes.return_value = self.hashes
        self.ctx.session_manager.remove.return_value = None
        self.module = OwnedLookupHarness(self.ctx, self.storage)
        self.worker = Worker("worker-incarnation-1", 0)

    def key(self, request_id="r", readers=1, world_size=1):
        return IPCCacheServerKey.from_token_ids("test", world_size, None,
            list(range(256 * len(self.hashes))), end=256 * len(self.hashes),
            request_id=request_id, num_kv_readers=readers)

    def seed(self, key=None, indices=None):
        key = key or self.key()
        keys = self.module._chunk_major_object_keys(key, self.hashes)
        for i in range(len(keys)) if indices is None else indices:
            self.l1.reserve_write([keys[i]], [False], Mock())
            self.l1.finish_write([keys[i]])
        return keys

    def begin(self, key=None, workers=None):
        return self.module.begin(key or self.key(), workers or (self.worker,))

    def bitmap(self, size, indices):
        bitmap = Bitmap(size)
        bitmap.batched_set(indices)
        return bitmap

    def stage_load(self, handle, local_indices):
        outer = self.module.jobs[handle.sequence]
        storage = self.storage.jobs[outer.storage_handle.sequence]
        child = self.controller.jobs[storage.downstream.sequence]
        request = child.request
        keys = [child.keys[i] for i in local_indices]
        reserved = self.l1.reserve_write(keys, [True] * len(keys), Mock())
        self.assertTrue(all(r[0] == L1Error.SUCCESS for r in reserved.values()))
        request.write_reserved_keys = keys
        request.write_reserved_objs = {key: reserved[key][1] for key in keys}
        request.load_plan = {0: self.bitmap(len(child.keys), local_indices)}
        request.pending_load_tasks = {0: child.handle.sequence}
        request.l2_adapter2readlocks = {0: self.bitmap(len(child.keys), local_indices)}
        request.phase = PrefetchPhase.PLAN_AND_LOAD
        self.controller._status_lookup_phase_count -= 1
        self.controller._status_load_phase_count += 1
        self.adapter.query_load_result.return_value = None
        return child

    def complete_load(self, child, indices):
        self.adapter.query_load_result.return_value = self.bitmap(child.request.load_plan[0].popcount(), indices)
        self.assertTrue(self.controller.poll_load(child.handle, {0}))
        self.assertTrue(self.controller.finish(child.handle))

    def finish_all(self, completion, succeeded=True):
        for ticket in completion.tickets:
            self.assertIsNotNone(self.module.claim_retrieve(ticket))
            self.assertTrue(self.module.finish_retrieve(ticket, succeeded=succeeded))

    def assert_drained(self):
        self.assertFalse(self.module.jobs)
        self.assertFalse(self.module._latest)
        self.assertFalse(self.module._prefetch_jobs)
        self.assertFalse(self.storage.jobs)
        self.assertFalse(self.controller.jobs)
        self.assertFalse(self.controller._completed_results)
        self.assertTrue(all(not e.read_lock.is_locked() and not e.write_lock.is_locked()
                            for e in self.l1._objects.values()))
