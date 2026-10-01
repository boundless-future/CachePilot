"""CPU transfer identity/completion contract; no wire or CUDA proof.

Only the bound server stream callback may declare submitted work stopped.
Future observations are diagnostics, never authority to retire an L1 lease.
"""
from dataclasses import dataclass, field
import threading
import uuid

from leased_lookup_contract import LeasedLookupHarness
from lmcache.v1.multiprocess.futures import DeviceMessagingFuture, MessagingFuture
from owned_lookup_contract import ReaderTicket


@dataclass(frozen=True)
class TransferHandle:
    registry: str
    sequence: int
    ticket: ReaderTicket


@dataclass
class TransferState:
    handle: TransferHandle
    phase: str = "acquiring"
    buffers: tuple = ()
    succeeded: bool | None = None
    stream_terminal: bool = False
    cleanup_attempted: bool = False
    errors: list = field(default_factory=list)
    observations: list = field(default_factory=list)


class TransferCompletionHarness:
    """Bind a whole-shard lease before handing its buffers to one submitter.

    enqueue calls run outside the registry lock. A reentrant callback is saved,
    but cleanup waits for the submitter to return, including on exceptions.
    A failed cleanup is retained and never retried by duplicate callbacks.
    """
    def __init__(self, lookup):
        if not isinstance(lookup, LeasedLookupHarness):
            raise ValueError("A leased Lookup registry is required")
        self.lookup = lookup
        self.identity = uuid.uuid4().hex
        self.sequence = 0
        self.states = {}
        self.tickets = {}
        self.gate = threading.RLock()

    def _state(self, handle):
        if not isinstance(handle, TransferHandle) or handle.registry != self.identity:
            return None
        state = self.states.get(handle.sequence)
        return state if state is not None and state.handle == handle else None

    def prepare(self, ticket):
        if not isinstance(ticket, ReaderTicket):
            return None
        with self.gate:
            if ticket in self.tickets:
                return None
            self.sequence += 1
            handle = TransferHandle(self.identity, self.sequence, ticket)
            state = TransferState(handle)
            self.states[handle.sequence] = state
            self.tickets[ticket] = handle
            try:
                claim = self.lookup.claim_retrieve(ticket)
                if claim is None:
                    state.phase = "unclaimed"
                    self.tickets.pop(ticket)
                    return None
                result = self.lookup.read_retrieve(ticket)
                if result is None or result.error:
                    state.phase = "preparation_failed"
                    state.errors.append(result.error if result else "No buffers delivered")
                else:
                    state.buffers = result.buffers
                    state.phase = "prepared"
            except Exception as exc:
                # Even acquisition can have an unknown outcome; keep identity.
                state.phase = "acquisition_unknown"
                state.errors.append(repr(exc))
            return handle

    def reject_unsubmitted(self, handle):
        with self.gate:
            state = self._state(handle)
            if state is None or state.phase not in ("prepared", "preparation_failed"):
                return False
            state.succeeded = False
            state.phase = "terminal"
            return self._cleanup(state)

    def enqueue(self, handle, submit):
        """submit(buffers, callback) returns a bool outcome, not DMA completion.

        callback must be dispatched only after ALL accesses on the bound server
        stream terminate. The CPU fixture supplies that promise; production
        needs the actual native stream completion mechanism and stream joining.
        Submission exceptions/invalid receipts conservatively retain the lease.
        """
        with self.gate:
            state = self._state(handle)
            if state is None or state.phase != "prepared":
                return False
            with self.lookup.gate:
                job = self.lookup._job(handle.ticket.lookup)
                if job is None:
                    state.errors.append("Lookup owner missing before submission")
                    state.phase = "acquisition_unknown"
                    return False
                if job.abandoned or job.error:
                    self.reject_unsubmitted(handle)
                    return False
                if not self._validate_submission(state):
                    return False
                # This transition precedes END under the same Lookup gate.
                # END after it must conservatively regard the work as in flight.
                state.phase = "submitting"
                buffers = state.buffers
        try:
            outcome = submit(buffers, lambda: self.stream_complete(handle))
            if type(outcome) is not bool:
                raise ValueError("Submission requires a bool outcome")
            error = None
        except Exception as exc:
            outcome, error = False, repr(exc)
        with self.gate:
            state.succeeded = outcome
            state.phase = "submission_unknown" if error else "submitted"
            if error:
                state.errors.append(error)
            if state.stream_terminal:
                state.phase = "terminal"
                self._cleanup(state)
            return outcome

    def _validate_submission(self, state):
        """Extension point under transfer/Lookup gates, before any submission."""
        return True

    def stream_complete(self, handle):
        """Server completion identity callback, never a client cancellation ACK."""
        with self.gate:
            state = self._state(handle)
            if (state is None or state.stream_terminal or state.phase not in
                    ("submitting", "submitted", "submission_unknown")):
                return False
            state.stream_terminal = True
            if state.phase == "submitting":
                return True
            state.phase = "terminal"
            return self._cleanup(state)

    def _cleanup(self, state):
        if state.cleanup_attempted:
            return False
        state.cleanup_attempted = True
        try:
            closed = self.lookup.finish_retrieve(
                state.handle.ticket, succeeded=state.succeeded, terminal=True)
            if not closed:
                raise RuntimeError("Lookup lease cleanup unresolved")
        except Exception as exc:
            state.errors.append(repr(exc))
            return False
        state.phase = "closed"
        state.buffers = ()
        return True

    def observe_future(self, handle, future):
        """Nonblocking diagnostic polling; even a complete event frees nothing."""
        if not isinstance(future, MessagingFuture):
            raise ValueError("An LMCache messaging future is required")
        with self.gate:
            state = self._state(handle)
            if state is None:
                return None
        kind = "device" if isinstance(future, DeviceMessagingFuture) else "message"
        done = False
        try:
            done = future.query()
            # Device query already processes the raw response. Avoid result(),
            # which synchronizes and has no bounded device timeout.
            value = future.result_ if done else None
            observation = (kind, "complete" if done else "pending", value)
        except Exception as exc:
            observation = (kind, "error", repr(exc))
        if done:
            if future.exception_ is not None:
                observation = (kind, "error", repr(future.exception_))
        with self.gate:
            state.observations.append(observation)
        return observation
