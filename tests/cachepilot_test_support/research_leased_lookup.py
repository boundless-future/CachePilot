"""Lookup slots own real CPU buffers until explicit consumer termination."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock

from cachepilot_test_support import research_lookup as lookup_fixture
from cachepilot_test_support import allocator as l1_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
try:
    from leased_lookup_contract import LeasedLookupHarness
    from leased_l1_contract import LeasedL1Harness
    from owned_lookup_contract import Worker
    from owned_prefetch_contract import JobHandle
    from lmcache.v1.distributed.api import AttnWindowDesc, ObjectKey
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    LeasedLookupHarness = None


class LeasedLookupSetup(lookup_fixture.OwnedLookupSetup):
    def setUp(self):
        lookup_fixture.OwnedLookupSetup.setUp(self)
        self.allocator = l1_fixture.ReusingAllocator()
        self.l1 = LeasedL1Harness(self.allocator, Mock())
        self.controller = lookup_fixture.OwnedPrefetchHarness(
            self.l1, Mock(), adapters={0: self.adapter},
            descriptors={0: lookup_fixture.SimpleNamespace(type_name="CPU-fixture")})
        self.storage = lookup_fixture.OwnedStorageHarness(self.l1, self.controller, Mock())
        self.module = LeasedLookupHarness(self.ctx, self.storage)

    def seed(self, key=None, indices=None, temporary=False):
        key = key or self.key()
        keys = self.module._chunk_major_object_keys(key, self.hashes)
        for i in range(len(keys)) if indices is None else indices:
            result = self.l1.reserve_write([keys[i]], [temporary], Mock())
            self.assertEqual(result[keys[i]][0], L1Error.SUCCESS)
            self.l1._objects[keys[i]].memory_obj.data[:] = b"K" * 64
            self.l1.finish_write([keys[i]])
        return keys

    def ready(self, *, readers=1, temporary=False, world_size=1):
        key = self.key(readers=readers, world_size=world_size)
        keys = self.seed(key, temporary=temporary)
        workers = tuple(Worker(f"worker-{r}-{i}", r)
                        for r in range(world_size) for i in range(readers))
        handle = self.begin(key, workers)
        completion = self.module.query_owned(handle)
        self.assertIsNotNone(completion)
        return keys, handle, completion.tickets

    def read(self, ticket):
        self.assertIsNotNone(self.module.claim_retrieve(ticket))
        result = self.module.read_retrieve(ticket)
        self.assertIsNone(result.error)
        self.assertTrue(result.buffers)
        return result

    def finish(self, ticket, succeeded=True):
        return self.module.finish_retrieve(ticket, succeeded=succeeded, terminal=True)

    def assert_drained(self):
        lookup_fixture.OwnedLookupSetup.assert_drained(self)
        self.assertFalse(self.module.accesses)
        self.assertFalse(self.l1.leases)

    def reset(self, keys):
        for key in keys:
            self.l1._objects[key].read_lock.core.reset()
