from dataclasses import replace
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock

import test_leased_l1_contract as fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    from owned_writer_contract import OwnedWriterHarness
    from lmcache.v1.distributed.error import L1Error
except ModuleNotFoundError:
    if os.environ.get('CACHEPILOT_REQUIRE_RESERVATION_NATIVE') == '1':
        raise
    OwnedWriterHarness = None


@unittest.skipUnless(OwnedWriterHarness, 'Needs native lock and pinned LMCache')
class WriterTests(unittest.TestCase):
    def setUp(self):
        self.allocator = fixture.ReusingAllocator()
        self.events = Mock()
        self.manager = OwnedWriterHarness(self.allocator, self.events, writer_ttl_ms=30)

    def reserve(self):
        status, handle, buffer = self.manager.reserve_writer('k', False, Mock())
        self.assertEqual(status, L1Error.SUCCESS)
        buffer.data[:] = b'W' * 64
        return handle, buffer

    def test_expired_writer_remains_unreadable_unevictable_and_unreusable(self):
        handle, buffer = self.reserve()
        time.sleep(.06)
        self.assertFalse(self.manager.is_key_evictable('k'))
        self.assertNotEqual(self.manager.delete(['k'])['k'], L1Error.SUCCESS)
        status, newer, _ = self.manager.reserve_writer('k', False, Mock())
        self.assertNotEqual(status, L1Error.SUCCESS)
        self.assertIsNone(newer)
        self.assertFalse(self.manager.reserve_read_owned(['k']).reservations)
        self.assertEqual(bytes(buffer.data), b'W' * 64)
        self.assertTrue(self.manager.finish_writer(handle, succeeded=True, terminal=True))
        read = self.manager.reserve_read_owned(['k'])
        self.assertEqual(read.native_result['k'][0], L1Error.SUCCESS)
        self.assertEqual(self.manager.finish_read_owned(read.reservations)[0].status, 'released')

    def test_failed_writer_discards_original_after_terminal_and_reuses_buffer(self):
        handle, buffer = self.reserve()
        self.assertTrue(self.manager.finish_writer(handle, succeeded=False, terminal=True))
        self.assertNotIn('k', self.manager._objects)
        new, reused = self.reserve()
        self.assertIs(reused, buffer)
        self.assertFalse(self.manager.finish_writer(handle, succeeded=True, terminal=True))
        self.assertTrue(self.manager._objects['k'].write_lock.is_locked())
        self.assertTrue(self.manager.finish_writer(new, succeeded=False, terminal=True))

    def test_foreign_identity_and_nonterminal_do_not_touch_writer(self):
        handle, buffer = self.reserve()
        self.assertFalse(self.manager.finish_writer(replace(handle, manager='foreign'),
                                                   succeeded=True, terminal=True))
        with self.assertRaises(ValueError):
            self.manager.finish_writer(handle, succeeded=False, terminal=False)
        self.assertFalse(self.allocator.freed)
        self.assertTrue(self.manager.finish_writer(handle, succeeded=False, terminal=True))

    def test_anonymous_finish_and_write_to_read_handoff_rejected(self):
        handle, _ = self.reserve()
        with self.assertRaises(RuntimeError):
            self.manager.finish_write(['k'])
        with self.assertRaises(RuntimeError):
            self.manager.finish_write_and_reserve_read_owned(['k'])
        self.assertTrue(self.manager.finish_writer(handle, succeeded=False, terminal=True))

    def test_publication_exception_retained_without_retry(self):
        handle, _ = self.reserve()
        self.events.publish.side_effect = RuntimeError('publication failed')
        self.assertFalse(self.manager.finish_writer(handle, succeeded=True, terminal=True))
        self.assertIn('publication failed', self.manager.writers[handle]['error'])
        self.assertFalse(self.manager.finish_writer(handle, succeeded=True, terminal=True))

    def test_shutdown_refuses_unknown_active_writer(self):
        handle, _ = self.reserve()
        with self.assertRaises(RuntimeError):
            self.manager.close()
        self.assertFalse(self.allocator.freed)
        self.assertTrue(self.manager.finish_writer(handle, succeeded=False, terminal=True))
