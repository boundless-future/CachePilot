"""Contract tests for LMCache lazy-offload request generations.

The project workstation does not install LMCache, so the module is skipped
there.  The same tests run against the pinned package in the GPU environment
and exercise the upstream registry/policy classes directly rather than a
local reimplementation.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


try:
    from lmcache.integration.vllm.lazy_offload_policy.eviction_aware import (
        EvictionAwarePolicyConfig,
        EvictionAwareStoreQueue,
    )
    from lmcache.integration.vllm.lazy_offload_state import (
        LazyOffloadRequestRegistry,
    )
except ModuleNotFoundError:
    EvictionAwarePolicyConfig = None
    EvictionAwareStoreQueue = None
    LazyOffloadRequestRegistry = None


def _metadata(request_id="r", start=0, end=256):
    return SimpleNamespace(
        request_id=request_id,
        op=SimpleNamespace(start=start, end=end),
    )


@unittest.skipUnless(
    LazyOffloadRequestRegistry is not None,
    "LMCache is only installed in the GPU validation environment",
)
class RegistryLifecycleTests(unittest.TestCase):
    def test_reset_orphans_batch_and_late_receipt_is_single_use(self):
        registry = LazyOffloadRequestRegistry()
        registry.arrive("r")
        registry.register_batch("r", [1, 2])

        registry.reset("r")
        self.assertTrue(registry.in_flight_is_orphaned("r"))
        batch = registry.complete_batch("r")
        self.assertTrue(batch.orphaned)
        self.assertEqual(batch.block_ids, (1, 2))
        with self.assertRaises(KeyError):
            registry.complete_batch("r")

    def test_finished_id_reuse_orphans_predecessor_batch(self):
        registry = LazyOffloadRequestRegistry()
        registry.arrive("r")
        registry.register_batch("r", [7])
        registry.finish("r")

        registry.arrive("r")
        self.assertFalse(registry.is_finished("r"))
        self.assertTrue(registry.in_flight_is_orphaned("r"))
        self.assertTrue(registry.has_in_flight("r"))
        batch = registry.complete_batch("r")
        self.assertTrue(batch.orphaned)

    def test_duplicate_batch_is_rejected_until_receipt(self):
        registry = LazyOffloadRequestRegistry()
        registry.arrive("r")
        registry.register_batch("r", [3])
        with self.assertRaises(RuntimeError):
            registry.register_batch("r", [4])
        registry.complete_batch("r")
        registry.register_batch("r", [4])
        self.assertEqual(registry.complete_batch("r").block_ids, (4,))


@unittest.skipUnless(
    EvictionAwareStoreQueue is not None,
    "LMCache is only installed in the GPU validation environment",
)
class PolicyLifecycleTests(unittest.TestCase):
    def make_policy(self):
        # add(), reset and failure paths do not inspect the pool.  Keeping the
        # pool fake makes this test independent of vLLM's GPU block classes.
        return EvictionAwareStoreQueue(
            EvictionAwarePolicyConfig(max_deferral_seconds=0),
            SimpleNamespace(),
        )

    def test_preemption_drop_clears_broken_marker_and_pending(self):
        policy = self.make_policy()
        policy.add(_metadata(), {1: "hash"})
        self.assertTrue(policy.has_pending_request("r"))
        self.assertEqual(policy.drop_request("r"), 1)
        self.assertFalse(policy.has_pending_request("r"))

        policy.add(_metadata(start=256, end=512), {2: "hash2"})
        self.assertTrue(policy.has_pending_request("r"))

    def test_failed_store_drops_suffix_and_rejects_later_admission(self):
        policy = self.make_policy()
        policy.add(_metadata(end=256), {1: "hash1"})
        policy.add(_metadata(start=256, end=512), {2: "hash2"})
        self.assertEqual(policy.mark_store_failed("r"), 2)
        self.assertFalse(policy.has_pending_request("r"))

        policy.add(_metadata(start=512, end=768), {3: "hash3"})
        self.assertFalse(policy.has_pending_request("r"))

    def test_id_reuse_discards_old_buffer_and_allows_new_generation(self):
        policy = self.make_policy()
        policy.add(_metadata(), {1: "hash1"})
        policy.discard_for_reuse("r")
        self.assertFalse(policy.has_pending_request("r"))
        policy.add(_metadata(), {9: "new-hash"})
        self.assertTrue(policy.has_pending_request("r"))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    unittest.main()
