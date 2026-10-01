"""Real pinned CPU -> CUDA and installed native dispatcher; opt-in on GPU.

Hasher/L2/context and worker allocation are fixtures, not production vLLM.
"""
from dataclasses import replace
import os
from pathlib import Path
import sys
import time
import unittest

import test_transfer_plan_contract as fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
GPU_ENABLED = os.environ.get("CACHEPILOT_RUN_CUDA_CONTRACT") == "1"
if GPU_ENABLED:
    import cupy
    import torch
    from lmcache.v1.multiprocess.native_completion import DeviceHostFuncDispatcher
    from native_transfer_runtime import NativeTransferRuntime, TargetArena


class PinnedBuffer:
    def __init__(self, size=64):
        from types import SimpleNamespace
        self.tensor = torch.empty(size, dtype=torch.uint8, pin_memory=True)
        self.data = memoryview(self.tensor.numpy()).cast('B')
        self.meta = SimpleNamespace(address=0)

    @property
    def raw_tensor(self):
        return self.tensor

    @property
    def data_ptr(self):
        return self.tensor.data_ptr()

    def get_size(self):
        return self.tensor.numel()

    def get_shapes(self):
        return [tuple(self.tensor.shape)]

    def get_dtypes(self):
        return [torch.uint8]


@unittest.skipUnless(GPU_ENABLED, "Explicit opt-in real CUDA test")
class NativeGPUTransferTests(unittest.TestCase):
    key = fixture.TransferPlanTests.key
    begin = fixture.TransferPlanTests.begin
    bitmap = fixture.TransferPlanTests.bitmap
    stage_load = fixture.TransferPlanTests.stage_load
    complete_load = fixture.TransferPlanTests.complete_load
    ready = fixture.TransferPlanTests.ready
    assert_drained = fixture.TransferPlanTests.assert_drained
    request_key = fixture.TransferPlanTests.request_key
    prepare = fixture.TransferPlanTests.prepare
    plan = fixture.TransferPlanTests.plan

    def setUp(self):
        fixture.TransferPlanTests.setUp(self)
        base = type(self.allocator)

        class PinnedAllocator(base):
            def allocate(inner, layout, count):
                from lmcache.v1.distributed.error import L1Error
                objects = [inner.pool.pop() if inner.pool else PinnedBuffer(getattr(inner, 'buffer_bytes', 64))
                           for _ in range(count)]
                for obj in objects:
                    obj.data[:] = b'N' * obj.get_size()
                return L1Error.SUCCESS, objects

            def free(inner, objects):
                for obj in objects:
                    inner.freed.append(obj)
                    obj.data[:] = b'F' * obj.get_size()
                    inner.pool.append(obj)

        self.allocator.__class__ = PinnedAllocator
        self.dispatcher = DeviceHostFuncDispatcher()
        self.dispatcher.start()
        self.runtime = NativeTransferRuntime(self.transfers, self.dispatcher)
        # Match LMCache cache_context: PyTorch owns the stream, CuPy borrows
        # its pointer. Destroying a CuPy-owned stream before PyTorch's pinned
        # allocator records frees can crash even after its work completed.
        self.torch_stream = torch.cuda.Stream()
        self.stream = cupy.cuda.ExternalStream(self.torch_stream.cuda_stream)
        self.arena = TargetArena(torch.zeros((100, 32), dtype=torch.uint8, device='cuda'),
                                 self.layout.incarnation)
        torch.cuda.synchronize()

    def tearDown(self):
        self.stream.synchronize()
        self.dispatcher.stop()

    def seed(self, key=None, indices=None, temporary=False):
        from lmcache.v1.distributed.error import L1Error
        from unittest.mock import Mock
        key = key or self.key()
        keys = self.module._chunk_major_object_keys(key, self.hashes)
        for i in range(len(keys)) if indices is None else indices:
            result = self.l1.reserve_write([keys[i]], [temporary], Mock())
            self.assertEqual(result[keys[i]][0], L1Error.SUCCESS)
            buffer = self.l1._objects[keys[i]].memory_obj
            buffer.data[:] = b'K' * buffer.get_size()
            self.l1.finish_write([keys[i]])
        return keys

    def setup_transfer(self):
        keys, job, ticket, handle = self.plan()
        buffers = tuple(self.l1._objects[k].memory_obj for k in keys)
        for i, buffer in enumerate(buffers):
            buffer.data[:] = bytes([65 + i]) * 64
            self.assertTrue(buffer.tensor.is_pinned())
        self.arena.allocate(ticket, range(6))
        return keys, job, ticket, handle, buffers

    def consumer(self, plan, stream, target, fail=False):
        with torch.cuda.stream(torch.cuda.ExternalStream(stream.ptr)):
            indices = [torch.tensor(plan.block_ids[0][i*2:i*2+2], device='cuda')
                       for i in range(len(plan.groups[0].buffers))]
            # A bounded real GPU kernel keeps consumer work queued while the
            # test exercises END/reset/free. It is not scheduler preemption.
            torch.cuda._sleep(150_000_000)
            for i, buffer in enumerate(plan.groups[0].buffers):
                value = buffer.tensor.to('cuda', non_blocking=True).reshape(2, 32)
                target.index_copy_(0, indices[i], value)
                if fail and i == 0:
                    raise RuntimeError('injected error AFTER actual first H2D/scatter enqueue')

    def wait_closed(self, handle):
        deadline = time.monotonic() + 10
        while self.transfers.states[handle.sequence].phase != 'closed' and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.transfers.states[handle.sequence].phase, 'closed')
        self.assertTrue(self.runtime.close(.5)['drained'])
        self.assertFalse(self.arena.active)

    def test_real_native_completion_pins_bytes_until_end_reset_and_block_reuse(self):
        keys, job, ticket, handle, buffers = self.setup_transfer()
        self.assertTrue(self.runtime.submit(handle, self.stream, self.arena, self.consumer))
        self.assertFalse(self.stream.done)
        self.module.end_owned(job)
        for key in keys:
            self.l1._objects[key].read_lock.core.reset()
        self.assertFalse(self.allocator.freed)
        self.assertFalse(self.arena.free(ticket, range(6)))
        self.assertEqual([bytes(b.data) for b in buffers], [b'A'*64, b'B'*64, b'C'*64])
        self.wait_closed(handle)
        self.assertEqual(self.arena.tensor[:6].cpu().numpy().tobytes(), b'A'*64+b'B'*64+b'C'*64)
        self.assertEqual(len(self.allocator.freed), 3)
        self.assertTrue(self.arena.free(ticket, range(6)))
        _, reused = self.allocator.allocate(None, 3)
        self.assertEqual({id(b) for b in reused}, {id(b) for b in buffers})
        self.assertEqual(self.arena.tensor[:6].cpu().numpy().tobytes(), b'A'*64+b'B'*64+b'C'*64)
        self.assert_drained()

    def test_partial_enqueue_exception_waits_for_actual_native_completion(self):
        _, job, ticket, handle, _ = self.setup_transfer()
        self.assertFalse(self.runtime.submit(handle, self.stream, self.arena,
            lambda p, s, t: self.consumer(p, s, t, fail=True)))
        self.module.end_owned(job)
        self.assertFalse(self.allocator.freed)
        self.assertFalse(self.arena.free(ticket, range(6)))
        self.wait_closed(handle)
        self.assertEqual(self.arena.tensor[:2].cpu().numpy().tobytes(), b'A'*64)
        self.assertEqual(self.arena.tensor[2:6].cpu().numpy().tobytes(), bytes(128))
        self.assertFalse(self.transfers.states[handle.sequence].succeeded)
        self.assertIn('AFTER actual', self.runtime.history[0]['error'])
        self.assert_drained()

    def test_wrong_worker_target_owner_rejected_before_gpu_submission(self):
        _, job, ticket, handle, _ = self.setup_transfer()
        self.arena.owners[0] = replace(ticket, worker=replace(ticket.worker, incarnation='restart'))
        with self.assertRaises(ValueError):
            self.runtime.submit(handle, self.stream, self.arena, self.consumer)
        self.assertFalse(self.runtime.pending)
        self.transfers.reject_unsubmitted(handle)
        self.module.end_owned(job)
        self.assert_drained()

    def test_missing_native_marker_is_quarantined_even_after_stream_done(self):
        _, job, ticket, handle, _ = self.setup_transfer()
        self.assertTrue(self.runtime.submit(handle, self.stream, self.arena,
            self.consumer, recorder=lambda *args: None))
        self.module.end_owned(job)
        self.stream.synchronize()
        self.assertTrue(self.stream.done)
        result = self.runtime.close(.05)
        self.assertFalse(result['drained'])
        self.assertEqual(result['unresolved'], [handle.sequence])
        self.assertFalse(self.allocator.freed)
        self.assertFalse(self.arena.free(ticket, range(6)))
        # Restore the actual native path to safely finish this experiment.
        from lmcache.v1.multiprocess.native_completion import submit_callback_to_stream
        payload = self.runtime.pending[handle.sequence]['payload']
        submit_callback_to_stream(self.stream, self.runtime.kind, payload)
        self.wait_closed(handle)
        self.assert_drained()

    def test_forged_and_duplicate_completion_do_not_release_original_lease(self):
        _, job, _, handle, _ = self.setup_transfer()
        self.runtime.submit(handle, self.stream, self.arena, self.consumer)
        payload = self.runtime.pending[handle.sequence]['payload']
        import msgspec
        forged = msgspec.structs.replace(payload, worker_incarnation='foreign')
        self.assertFalse(self.runtime._complete(forged))
        self.assertFalse(self.allocator.freed)
        self.module.end_owned(job)
        self.wait_closed(handle)
        self.assertFalse(self.runtime._complete(payload))
        self.assertEqual(len(self.allocator.freed), 3)
        self.assert_drained()

    def test_real_lmcache_object_group_kernel_and_context(self):
        self._run_object_group_transfer(via_wire=False)

    def test_versioned_socket_query_retrieve_to_native_kernel(self):
        self._run_object_group_transfer(via_wire=True)

    def _run_object_group_transfer(self, via_wire):
        from lmcache import device_ops
        from lmcache.v1.platform.devices.cuda.cache_context import GPUCacheContext
        from lmcache.v1.multiprocess.object_group_transfer import transfer_kv_per_object_group
        from transfer_plan_contract import KernelLayout
        import lmcache.lmcache_native as native

        class SameProcessTensor:
            def __init__(inner, tensor):
                inner.tensor = tensor

            def to_tensor(inner):
                return inner.tensor

        target = torch.zeros((100, 2, 16, 1, 8), dtype=torch.bfloat16, device='cuda')
        context = GPUCacheContext([SameProcessTensor(target)], lmcache_tokens_per_chunk=256)
        self.assertTrue(hasattr(device_ops, 'execute_object_group_transfer'))
        self.layout = replace(self.layout, incarnation='native-layout', object_bytes=(8192,),
                              kernels=(KernelLayout(0, 16, 100),))
        self.transfers.register_layout(self.layout)
        self.allocator.buffer_bytes = 8192
        self.arena = TargetArena(target, self.layout.incarnation)
        self.stream = context.cupy_stream
        if via_wire:
            import msgspec
            import zmq
            from owned_lookup_contract import ReaderTicket
            from owned_wire_protocol import OwnedSessionRuntime, QueryReplyV1, RetrieveV1
            from owned_socket_runtime import OwnedSocketEndpoint, QueryV1, TransferV1, ReplyV1
            keys = self.seed(temporary=True)
            sessions = OwnedSessionRuntime(self.module, self.transfers)
            sessions.register(self.layout.worker)
            job = sessions.begin(self.key(), (self.layout.worker,))
            ticket = ReaderTicket(job, self.layout.worker)
        else:
            keys, job, ticket, handle = self.plan(blocks=[list(range(48))])
        for i, key in enumerate(keys):
            self.l1._objects[key].memory_obj.data[:] = bytes([65+i]) * 8192
        self.arena.allocate(ticket, range(48))
        torch.cuda.synchronize()

        def consumer(plan, stream, tensor):
            self.assertEqual(stream.ptr, context.stream.cuda_stream)
            with torch.cuda.stream(context.stream):
                ids = [torch.tensor(plan.block_ids[0], device='cuda', dtype=torch.int64)]
                torch.cuda._sleep(800_000_000 if via_wire else 150_000_000)
                transfer_kv_per_object_group(context, ids, plan.groups[0].buffers,
                    0, 2, 0, native.TransferDirection.H2D, transfer_key=str(plan.handle.sequence))

        if via_wire:
            server = OwnedSocketEndpoint(sessions, self.runtime, ticket.worker,
                job, self.request_key(), self.stream, self.arena, consumer)
            client_context = zmq.Context()
            client = client_context.socket(zmq.REQ)
            client.setsockopt(zmq.LINGER, 0)
            client.setsockopt(zmq.RCVTIMEO, 5000)
            client.connect(server.address)
            def exchange(message):
                client.send(msgspec.msgpack.encode(message))
                return msgspec.msgpack.decode(client.recv(), type=ReplyV1)
            try:
                rejected = exchange(QueryV1(2, job.server, job.sequence, job.external_request_id))
                self.assertEqual(rejected.status, 'rejected')
                query = exchange(QueryV1(1, job.server, job.sequence, job.external_request_id))
                self.assertEqual(query.status, 'ready')
                result = msgspec.msgpack.decode(query.data, type=QueryReplyV1)
                self.assertEqual(result.hit_chunks, 3)
                self.assertEqual(tuple(t.original() for t in result.tickets), (ticket,))
                packet = RetrieveV1(1, result.tickets[0], self.layout.incarnation,
                    (tuple(range(48)),), 0, 768)
                invalid = msgspec.structs.replace(packet, block_ids=((0, 1),))
                self.assertEqual(exchange(TransferV1(1, msgspec.msgpack.encode(invalid))).status, 'rejected')
                self.assertFalse(self.module.accesses)
                reply = exchange(TransferV1(1, msgspec.msgpack.encode(packet)))
                self.assertEqual(reply.status, 'submitted', self.runtime.pending)
                handle, = server.handles
            finally:
                client.close()
                client_context.term()
                server.close()
        else:
            self.assertTrue(self.runtime.submit(handle, self.stream, self.arena, consumer))
        self.assertFalse(self.stream.done)
        self.module.end_owned(job)
        self.assertFalse(self.allocator.freed)
        self.assertFalse(self.arena.free(ticket, range(48)))
        self.wait_closed(handle)
        self.assertEqual(target[:48].cpu().view(torch.uint8).numpy().tobytes(),
                         b'A'*8192+b'B'*8192+b'C'*8192)
        self.assert_drained()
        context.close()
