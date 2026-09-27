"""Candidate wrapper contracts against the pinned *real* LookupModule.

Only storage and layouts are faked; native Bitmap and MP handler code are real.
These tests exercise no GPU, but require the server's installed LMCache build.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lookup_server_reclaim import install_reclaim

try:
    from lmcache.lmcache_native import Bitmap
    from lmcache.v1.distributed.api import AttnWindowDesc, PrefetchHandle
    from lmcache.v1.multiprocess.modules.lookup import LookupModule
    from lmcache.v1.multiprocess.request_handler import get_request_handler_options
except ModuleNotFoundError:
    LookupModule = None


@unittest.skipUnless(LookupModule is not None, "Needs the pinned LMCache environment")
class LookupReclaimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = install_reclaim(background=False)

    @classmethod
    def tearDownClass(cls):
        for name, method in cls.original.items():
            setattr(LookupModule, name, method)

    def setUp(self):
        self.ctx = Mock()
        self.ctx.chunk_size = 256
        self.ctx.event_bus.has_subscribers.return_value = False
        self.ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1])
        self.ctx.layout_desc_registry.find_group_layout_descs.return_value = {0: Mock()}
        self.ctx.token_hasher.compute_chunk_hashes.return_value = [b'a', b'b', b'c']
        self.ctx.session_manager.remove.return_value = None
        self.handles = []
        self.ready = {}
        self.polls = []
        self.ctx.storage_manager.submit_prefetch_task.side_effect = self.submit
        self.ctx.storage_manager.query_prefetch_status.side_effect = self.poll
        with patch.object(LookupModule, "_setup_metrics"):
            self.module = LookupModule(self.ctx)
        self.module._chunk_major_object_keys = Mock(return_value=["a", "b", "c"])
        self.state = self.module._cachepilot_reclaim
        self.addCleanup(self.module.close)

    def submit(self, spec, external_request_id):
        handle = PrefetchHandle(len(self.handles), external_request_id,
                                (), 0, len(spec.keys), 0.0)
        self.handles.append(handle)
        return handle

    def poll(self, handle):
        self.polls.append(handle.prefetch_request_id)
        # The controller result is destructive; a second consumer gets None.
        return self.ready.pop(handle.prefetch_request_id, None)

    def lookup(self, request_id="r", readers=1):
        key = SimpleNamespace(request_id=request_id, model_name="model", world_size=1,
                              token_ids=(1, 2, 3), end=768, cache_salt="",
                              require_num_kv_readers=lambda: readers)
        self.module.lookup(key, 1)
        return self.handles[-1]

    def complete(self, handle, indices=(0, 1, 2)):
        bitmap = Bitmap(3)
        bitmap.batched_set(indices)
        self.ready[handle.prefetch_request_id] = bitmap

    def assert_drained(self, releases=1):
        self.assertFalse(self.state.abandoned)
        self.assertFalse(self.state.keys)
        self.assertFalse(self.module._prefetch_jobs)
        self.assertFalse(self.ready)
        self.assertEqual(self.state.completed, releases)

    def test_completion_before_end_releases_once_and_duplicate_end_is_noop(self):
        handle = self.lookup(readers=2)
        self.complete(handle)
        self.module.end_session("r")
        self.module.end_session("r")
        self.state.reap_once()
        self.ctx.storage_manager.finish_read_prefetched.assert_called_once_with(
            ["a", "b", "c"], read_locks=2)
        self.assert_drained()

    def test_pending_end_late_completion_and_query_do_not_recreate_session(self):
        handle = self.lookup()
        self.ctx.session_manager.reset_mock()
        self.module.end_session("r")
        for _ in range(3):
            self.assertEqual(self.module.query_prefetch_status("r"), 0)
            self.state.reap_once()
        self.assertEqual(len(self.state.abandoned), 1)
        self.ctx.storage_manager.finish_read_prefetched.assert_not_called()
        self.complete(handle)
        self.state.reap_once()
        self.ctx.session_manager.get_or_create.assert_not_called()
        self.assert_drained()

    def test_wait_timeout_does_not_unlock_pending_job(self):
        self.lookup()
        self.ctx.storage_manager.wait_prefetch_status.return_value = False
        self.assertIsNone(self.module.wait_prefetch_status("r", 0.01))
        self.module.end_session("r")
        self.assertEqual(self.module.report_status()["abandoned_prefetch_jobs"], 1)
        self.ctx.storage_manager.finish_read_prefetched.assert_not_called()

    def test_id_reuse_old_completion_does_not_consume_new_job(self):
        old = self.lookup()
        self.module.end_session("r")
        new = self.lookup()
        self.complete(old)
        self.state.reap_once()
        self.assertIs(self.module._prefetch_jobs["r"].handle, new)
        self.complete(new)
        self.module.end_session("r")
        self.assert_drained(releases=2)
        self.assertEqual(self.ctx.storage_manager.finish_read_prefetched.call_count, 2)

    def test_normal_query_transfers_ownership_no_extra_unlock_on_end(self):
        handle = self.lookup()
        self.complete(handle)
        self.assertEqual(self.module.query_prefetch_status("r"), 3)
        self.module.end_session("r")
        self.ctx.storage_manager.finish_read_prefetched.assert_not_called()
        self.assert_drained(releases=0)

    def test_partial_result_releases_exact_bitmap_not_entire_prefix(self):
        handle = self.lookup()
        self.complete(handle, (0, 2))
        self.module.end_session("r")
        self.ctx.storage_manager.finish_read_prefetched.assert_called_once_with(
            ["a", "c"], read_locks=1)
        self.assert_drained()

    def test_empty_completed_result_consumed_without_unlock(self):
        handle = self.lookup()
        self.complete(handle, ())
        self.module.end_session("r")
        self.ctx.storage_manager.finish_read_prefetched.assert_not_called()
        self.assert_drained()

    def test_unlock_exception_remains_visible_without_unsafe_retry(self):
        handle = self.lookup()
        self.complete(handle)
        self.ctx.storage_manager.finish_read_prefetched.side_effect = RuntimeError("partial unlock")
        with self.assertLogs("cachepilot.lookup_reclaim", "ERROR"):
            self.module.end_session("r")
        self.state.reap_once()
        self.assertEqual(self.ctx.storage_manager.finish_read_prefetched.call_count, 1)
        self.assertEqual(self.module.report_status()["reclaim_failed_jobs"], 1)
        self.assertEqual(self.state.completed, 0)

    def test_query_exception_retains_failed_owner(self):
        self.lookup()
        self.ctx.storage_manager.query_prefetch_status.side_effect = RuntimeError("controller failure")
        with self.assertLogs("cachepilot.lookup_reclaim", "ERROR"):
            self.module.end_session("r")
        self.assertEqual(self.module.report_status()["reclaim_failed_jobs"], 1)
        self.ctx.storage_manager.finish_read_prefetched.assert_not_called()

    def test_many_completed_jobs_do_not_accumulate_tombstones(self):
        for i in range(100):
            handle = self.lookup(str(i))
            self.complete(handle)
            self.module.end_session(str(i))
        self.assert_drained(releases=100)

    def test_concurrent_duplicate_end_reclaims_once(self):
        handle = self.lookup()
        self.complete(handle)
        barrier = threading.Barrier(4)
        def end():
            barrier.wait(timeout=2)
            self.module.end_session("r")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(end) for _ in range(4)]
            for future in futures:
                future.result(timeout=3)
        self.ctx.storage_manager.finish_read_prefetched.assert_called_once()
        self.assert_drained()

    def test_waiter_for_old_generation_does_not_consume_reused_id(self):
        old = self.lookup()
        entered, release = threading.Event(), threading.Event()
        def wait(*args):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test did not release waiter")
            return True
        self.ctx.storage_manager.wait_prefetch_status.side_effect = wait
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.module.wait_prefetch_status, "r", 1)
            self.assertTrue(entered.wait(1))
            try:
                self.module.end_session("r")
                new = self.lookup()
                self.complete(new)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=2), 0)
        self.assertIn(new.prefetch_request_id, self.ready)
        self.complete(old)
        self.state.reap_once()
        self.module.end_session("r")
        self.assert_drained(releases=2)

    def test_concurrent_query_and_end_have_one_consumer(self):
        handle = self.lookup()
        self.complete(handle)
        barrier = threading.Barrier(2)
        def invoke(method):
            barrier.wait(timeout=2)
            return method("r")
        with ThreadPoolExecutor(max_workers=2) as pool:
            query = pool.submit(invoke, self.module.query_prefetch_status)
            end = pool.submit(invoke, self.module.end_session)
            result = query.result(timeout=3)
            end.result(timeout=3)
        self.assertEqual(self.polls.count(handle.prefetch_request_id), 1)
        self.assertEqual(self.ctx.storage_manager.finish_read_prefetched.call_count,
                         1 if result == 0 else 0)

    def test_background_worker_reclaims_without_future_request(self):
        from lookup_server_reclaim import ReclaimState
        self.state.close()
        self.state = ReclaimState(self.module, background=True)
        self.module._cachepilot_reclaim = self.state
        handle = self.lookup()
        self.module.end_session("r")
        released = threading.Event()
        self.ctx.storage_manager.finish_read_prefetched.side_effect = lambda *a, **k: released.set()
        with self.state.gate:
            self.complete(handle)
        self.assertTrue(released.wait(2))
        with self.state.gate:
            self.assert_drained()
        self.module.close()
        self.assertFalse(self.state.thread.is_alive())

    def test_handler_metadata_preserved(self):
        for name in ("lookup", "query_prefetch_status", "end_session", "wait_prefetch_status"):
            self.assertEqual(get_request_handler_options(getattr(LookupModule, name)),
                             get_request_handler_options(self.original[name]))


if __name__ == "__main__":
    unittest.main()
