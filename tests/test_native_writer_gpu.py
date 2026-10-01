"""Actual D2H success/partial enqueue, native marker, writer TTL and reuse."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock

import test_leased_l1_contract as fixture
from test_native_transfer_gpu import PinnedBuffer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
ENABLED = os.environ.get('CACHEPILOT_RUN_CUDA_CONTRACT') == '1'
if ENABLED:
    import cupy
    import torch
    from lmcache.v1.distributed.error import L1Error
    from lmcache.v1.multiprocess.native_completion import DeviceHostFuncDispatcher
    from owned_writer_contract import OwnedWriterHarness
    from native_writer_runtime import NativeWriterRuntime


@unittest.skipUnless(ENABLED, 'Explicit opt-in real CUDA writer test')
class NativeGPUWriterTests(unittest.TestCase):
    def setUp(self):
        class Allocator(fixture.ReusingAllocator):
            def allocate(inner, layout, count):
                return L1Error.SUCCESS, [inner.pool.pop() if inner.pool else PinnedBuffer()
                                        for _ in range(count)]
            def free(inner, objects):
                inner.observed_before_free = [bytes(buffer.data) for buffer in objects]
                super().free(objects)
        self.allocator = Allocator()
        self.manager = OwnedWriterHarness(self.allocator, Mock(), writer_ttl_ms=30)
        self.dispatcher = DeviceHostFuncDispatcher()
        self.dispatcher.start()
        self.runtime = NativeWriterRuntime(self.manager, self.dispatcher)
        self.torch_stream = torch.cuda.Stream()
        self.stream = cupy.cuda.ExternalStream(self.torch_stream.cuda_stream)
        self.source = torch.full((64,), 75, dtype=torch.uint8, device='cuda')
        torch.cuda.synchronize()

    def tearDown(self):
        self.stream.synchronize()
        self.dispatcher.stop()

    def run_writer(self, fail):
        status, handle, buffer = self.manager.reserve_writer('writer', False, Mock())
        self.assertEqual(status, L1Error.SUCCESS)
        buffer.data[:] = b'N' * 64
        def consumer(buffer, stream):
            with torch.cuda.stream(self.torch_stream):
                torch.cuda._sleep(800_000_000)
                if fail:
                    buffer.tensor[:32].copy_(self.source[:32], non_blocking=True)
                    raise RuntimeError('partial D2H enqueue')
                buffer.tensor.copy_(self.source, non_blocking=True)
        self.assertEqual(self.runtime.submit(handle, self.stream, consumer), not fail)
        self.assertFalse(self.stream.done)
        time.sleep(.06)
        self.assertFalse(self.manager.is_key_evictable('writer'))
        self.assertNotEqual(self.manager.delete(['writer'])['writer'], L1Error.SUCCESS)
        self.assertFalse(self.manager.reserve_read_owned(['writer']).reservations)
        self.assertFalse(self.allocator.freed)
        self.assertTrue(self.runtime.close(5)['drained'])
        self.assertFalse(self.manager.writers)
        self.assertEqual(self.runtime.completed[0]['succeeded'], not fail)
        if fail:
            self.assertNotIn('writer', self.manager._objects)
            self.assertEqual(len(self.allocator.freed), 1)
            self.assertEqual(self.allocator.observed_before_free, [b'K' * 32 + b'N' * 32])
            _, newer, reused = self.manager.reserve_writer('writer', False, Mock())
            self.assertIs(reused, buffer)
            self.assertFalse(self.manager.finish_writer(handle, succeeded=True, terminal=True))
            self.assertTrue(self.manager.finish_writer(newer, succeeded=False, terminal=True))
        else:
            read = self.manager.reserve_read_owned(['writer'])
            self.assertEqual(read.native_result['writer'][0], L1Error.SUCCESS)
            self.assertEqual(bytes(buffer.data), b'K' * 64)
            self.manager.finish_read_owned(read.reservations)

    def test_actual_d2h_publish_after_native_completion_and_expired_writer(self):
        self.run_writer(False)

    def test_partial_d2h_exception_waits_then_discards_and_reuses_writer(self):
        self.run_writer(True)
