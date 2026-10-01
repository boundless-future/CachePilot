"""Opt-in CUDA prototype over the owned Lookup chain, not an MP server patch.

Only a server native callback can retire a transfer. A missing callback keeps
both source and target leases; close never turns a timeout into completion.
"""
from dataclasses import dataclass
import threading
import time
import uuid

import msgspec
import torch
from lmcache.v1.multiprocess.native_completion import submit_callback_to_stream


class CompletionV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: int
    registry: str
    sequence: int
    lookup_server: str
    lookup_sequence: int
    request_id: str
    worker_incarnation: str
    rank: int
    nonce: str


def completion_payload(handle, nonce):
    ticket = handle.ticket
    return CompletionV1(1, handle.registry, handle.sequence, ticket.lookup.server,
                        ticket.lookup.sequence, ticket.lookup.external_request_id,
                        ticket.worker.incarnation, ticket.worker.rank, nonce)


@dataclass(frozen=True)
class TensorLayout:
    shape: tuple
    stride: tuple
    dtype: str
    device: str
    pointer: int
    element_bytes: int

    @classmethod
    def capture(cls, tensor):
        if not isinstance(tensor, torch.Tensor) or not tensor.is_cuda:
            raise ValueError("A server-registered CUDA tensor is required")
        return cls(tuple(tensor.shape), tuple(tensor.stride()), str(tensor.dtype),
                   str(tensor.device), tensor.data_ptr(), tensor.element_size())


class TargetArena:
    """Explicit target ownership for the standalone tensor experiment.

    This is not a substitute for vLLM BlockPool references. All mutation must
    pass through this arena; bypassing it is outside the prototype contract.
    """
    def __init__(self, tensor, registration):
        if tensor.ndim < 2 or not tensor.is_contiguous():
            raise ValueError("Contiguous block-major CUDA tensor required")
        self.tensor = tensor
        self.layout = TensorLayout.capture(tensor)
        self.registration = registration
        self.owners = {}
        self.active = {}
        self.gate = threading.RLock()

    def allocate(self, owner, blocks):
        blocks = tuple(blocks)
        with self.gate:
            if (not blocks or len(set(blocks)) != len(blocks)
                    or any(type(b) is not int or not 0 <= b < self.tensor.shape[0]
                           or b in self.owners for b in blocks)):
                raise ValueError("Target blocks unavailable")
            for block in blocks:
                self.owners[block] = owner

    def claim(self, handle, blocks, registration):
        blocks = tuple(blocks)
        with self.gate:
            if (registration != self.registration or TensorLayout.capture(self.tensor) != self.layout
                    or handle in self.active or not blocks or len(set(blocks)) != len(blocks)
                    or any(self.owners.get(b) != handle.ticket for b in blocks)
                    or any(set(blocks).intersection(ids) for ids in self.active.values())):
                raise ValueError("Target allocation owner/layout changed")
            self.active[handle] = blocks

    def terminal(self, handle):
        with self.gate:
            return self.active.pop(handle, None) is not None

    def free(self, owner, blocks):
        blocks = tuple(blocks)
        with self.gate:
            if (any(self.owners.get(b) != owner for b in blocks)
                    or any(set(blocks).intersection(ids) for ids in self.active.values())):
                return False
            for b in blocks:
                del self.owners[b]
            return True


class NativeTransferRuntime:
    def __init__(self, transfers, dispatcher):
        self.transfers = transfers
        self.dispatcher = dispatcher
        self.kind = "cachepilot.transfer.v1." + uuid.uuid4().hex
        self.pending = {}
        self.history = []
        self.gate = threading.RLock()
        self.accepting = True
        dispatcher.register(self.kind, self._complete, CompletionV1)

    def _complete(self, payload):
        with self.gate:
            entry = self.pending.get(payload.sequence)
            if entry is None or payload != entry["payload"] or payload.version != 1:
                return False
            entry["seen"] = True
            # Callback can race submit return. Source cleanup handles that;
            # target retirement must wait for the same submission boundary.
            entry["callback"]()
            if entry["returned"]:
                self._retire(entry)
            return True

    def _retire(self, entry):
        entry["arena"].terminal(entry["handle"])
        self.pending.pop(entry["handle"].sequence, None)
        self.history.append(dict(sequence=entry["handle"].sequence,
                                 terminal_ns=time.monotonic_ns(),
                                 error=entry["error"], native_callback=True))

    def submit(self, handle, stream, arena, consumer, *, recorder=submit_callback_to_stream):
        """consumer(plan, stream, target) must enqueue ALL accesses on stream.

        Exception after partial enqueue still places a marker after those
        accesses. Unjoined side streams are forbidden by this interface.
        """
        with self.gate:
            if not self.accepting or self.pending.get(handle.sequence):
                return False
            plan = self.transfers.plans.get(handle)
            if plan is None or len(plan.block_ids) != 1:
                raise ValueError("Prototype supports one target kernel group")
            arena.claim(handle, plan.block_ids[0], plan.layout.incarnation)
        entry = dict(handle=handle, arena=arena, returned=False, seen=False,
                     payload=completion_payload(handle, uuid.uuid4().hex),
                     callback=None, error=None, submitted_ns=time.monotonic_ns())
        with self.gate:
            self.pending[handle.sequence] = entry

        def submit_plan(plan, callback):
            entry["callback"] = callback
            try:
                consumer(plan, stream, arena.tensor)
                outcome = True
            except Exception as exc:
                entry["error"] = repr(exc)
                outcome = False
            # The pinned native implementation silently discards host-launch
            # errors. No return value is treated as completion evidence.
            try:
                recorder(stream, self.kind, entry["payload"])
            except Exception as exc:
                entry["error"] = "; ".join(filter(None, (entry["error"], repr(exc))))
                raise
            return outcome

        outcome = self.transfers.enqueue_plan(handle, submit_plan)
        with self.gate:
            entry["returned"] = True
            if entry["callback"] is None:
                # Registry rejected before consumer ran: no target writes.
                arena.terminal(handle)
                self.pending.pop(handle.sequence, None)
            elif entry["seen"]:
                self._retire(entry)
        return outcome

    def close(self, timeout=1.0):
        with self.gate:
            self.accepting = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.gate:
                if not self.pending:
                    return {"drained": True, "unresolved": []}
            time.sleep(.005)
        with self.gate:
            return {"drained": not self.pending,
                    "unresolved": [entry["payload"].sequence for entry in self.pending.values()]}
