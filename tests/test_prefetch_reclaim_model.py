import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prefetch_reclaim_model import JobState, PrefetchReclaimer


class PrefetchReclaimModelTests(unittest.TestCase):
    def test_end_before_completion_defers_and_reclaims_on_completion(self):
        model = PrefetchReclaimer()
        handle = model.register("r", 17)

        self.assertEqual(model.end_session(handle), "deferred")
        self.assertEqual(model.active_jobs(), 1)
        self.assertEqual(model.complete(handle, 17), "reclaimed")
        self.assertEqual(model.state(handle), JobState.RECLAIMED)
        self.assertEqual(model.active_jobs(), 0)
        self.assertEqual(model.unreleased_chunks(), 0)
        self.assertEqual(model.release_events, [handle])

    def test_completion_before_end_reclaims_at_end(self):
        model = PrefetchReclaimer()
        handle = model.register("r", 17)

        self.assertEqual(model.complete(handle, 17), "completed")
        self.assertEqual(model.end_session(handle), "reclaimed")
        self.assertEqual(model.release_events, [handle])

    def test_normal_consume_then_duplicate_end_is_exactly_once(self):
        model = PrefetchReclaimer()
        handle = model.register("r", 4)
        model.complete(handle, 4)
        self.assertEqual(model.consume(handle), "reclaimed")
        self.assertEqual(model.end_session(handle), "already_reclaimed")
        self.assertEqual(model.complete(handle, 4), "already_reclaimed")
        self.assertEqual(model.release_events, [handle])

    def test_failure_after_cancel_reclaims(self):
        model = PrefetchReclaimer()
        handle = model.register("r", 9)
        self.assertEqual(model.end_session(handle), "deferred")
        self.assertEqual(model.fail(handle), "reclaimed")
        self.assertEqual(model.unreleased_chunks(), 0)

    def test_request_id_reuse_keeps_generations_separate(self):
        model = PrefetchReclaimer()
        old = model.register("reused", 3)
        new = model.register("reused", 5)

        self.assertNotEqual(old.generation, new.generation)
        self.assertEqual(model.end_session(new), "deferred")
        self.assertEqual(model.complete(old, 3), "completed")
        self.assertEqual(model.end_session(old), "reclaimed")
        self.assertEqual(model.complete(new, 5), "reclaimed")
        self.assertEqual(model.release_events, [old, new])

    def test_invalid_completion_and_negative_lock_count_are_rejected(self):
        model = PrefetchReclaimer()
        with self.assertRaises(ValueError):
            model.register("r", -1)
        handle = model.register("r", 1)
        with self.assertRaises(RuntimeError):
            model.consume(handle)
        model.fail(handle)
        with self.assertRaises(RuntimeError):
            model.complete(handle, 1)


if __name__ == "__main__":
    unittest.main()
