"""CPU transfer-plan checks for original ticket/range/group/buffer binding."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock

from cachepilot_test_support import research_leased_lookup as lookup_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
try:
    from transfer_plan_contract import KernelLayout, PlannedTransferHarness, RegisteredLayout
    from owned_lookup_contract import Worker
    from lmcache.v1.distributed.api import AttnWindowDesc
    from lmcache.v1.multiprocess.custom_types import IPCCacheServerKey
except ModuleNotFoundError:
    if os.environ.get("CACHEPILOT_REQUIRE_RESERVATION_NATIVE") == "1":
        raise
    PlannedTransferHarness = None


class TransferPlanSetup(lookup_fixture.LeasedLookupSetup):
    def setUp(self):
        lookup_fixture.LeasedLookupSetup.setUp(self)
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
