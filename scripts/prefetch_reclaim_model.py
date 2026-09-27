"""Pure-Python model for MP lookup prefetch reclamation.

The model captures the ownership rule needed by a server-side fix without
importing vLLM, LMCache, CUDA, or multiprocessing code.  A prefetch can finish
before or after END_SESSION; either order must release its read locks exactly
once.  Generation-aware handles keep a reused request id from completing the
wrong job.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple


class JobState(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    RECLAIMED = "reclaimed"


@dataclass(frozen=True)
class PrefetchHandle:
    request_id: str
    generation: int


@dataclass
class _Job:
    handle: PrefetchHandle
    locked_chunks: int
    state: JobState = JobState.PENDING
    hit_chunks: int = 0
    cleanup_requested: bool = False
    released: bool = False


class PrefetchReclaimer:
    """Reference lifecycle for server-owned deferred cleanup.

    ``register`` returns a generation token.  ``end_session`` only marks the
    matching generation abandoned; it never forgets a pending job.  A later
    completion/failure callback then performs the deferred release.  All
    release paths converge on ``_reclaim`` so duplicate callbacks are harmless.
    """

    def __init__(self) -> None:
        self._next_generation = 1
        self._jobs: Dict[Tuple[str, int], _Job] = {}
        self.release_events: List[PrefetchHandle] = []

    def register(self, request_id: str, locked_chunks: int) -> PrefetchHandle:
        if locked_chunks < 0:
            raise ValueError("locked_chunks must be non-negative")
        handle = PrefetchHandle(request_id, self._next_generation)
        self._next_generation += 1
        self._jobs[(handle.request_id, handle.generation)] = _Job(
            handle=handle, locked_chunks=locked_chunks
        )
        return handle

    def end_session(self, handle: PrefetchHandle) -> str:
        job = self._get(handle)
        if job.released:
            return "already_reclaimed"
        job.cleanup_requested = True
        if job.state is not JobState.PENDING:
            self._reclaim(job)
            return "reclaimed"
        return "deferred"

    def complete(self, handle: PrefetchHandle, hit_chunks: int) -> str:
        if hit_chunks < 0:
            raise ValueError("hit_chunks must be non-negative")
        job = self._get(handle)
        if job.released:
            return "already_reclaimed"
        if job.state is JobState.PENDING:
            job.state = JobState.COMPLETED
            job.hit_chunks = hit_chunks
        elif job.state is not JobState.COMPLETED:
            raise RuntimeError(f"cannot complete {job.state.value} job")
        if job.cleanup_requested:
            self._reclaim(job)
            return "reclaimed"
        return "completed"

    def fail(self, handle: PrefetchHandle) -> str:
        job = self._get(handle)
        if job.released:
            return "already_reclaimed"
        if job.state is JobState.PENDING:
            job.state = JobState.FAILED
        elif job.state is not JobState.FAILED:
            raise RuntimeError(f"cannot fail {job.state.value} job")
        if job.cleanup_requested:
            self._reclaim(job)
            return "reclaimed"
        return "failed"

    def consume(self, handle: PrefetchHandle) -> str:
        """Model normal query-prefetch-status + free-lookup-locks."""
        job = self._get(handle)
        if job.released:
            return "already_reclaimed"
        if job.state is JobState.PENDING:
            raise RuntimeError("cannot consume a pending prefetch")
        self._reclaim(job)
        return "reclaimed"

    def state(self, handle: PrefetchHandle) -> JobState:
        return self._get(handle).state

    def active_jobs(self) -> int:
        return sum(not job.released for job in self._jobs.values())

    def unreleased_chunks(self) -> int:
        return sum(job.locked_chunks for job in self._jobs.values() if not job.released)

    def _get(self, handle: PrefetchHandle) -> _Job:
        try:
            return self._jobs[(handle.request_id, handle.generation)]
        except KeyError as exc:
            raise KeyError(f"unknown prefetch handle: {handle}") from exc

    def _reclaim(self, job: _Job) -> None:
        if job.released:
            return
        job.released = True
        job.state = JobState.RECLAIMED
        self.release_events.append(job.handle)


__all__ = ["JobState", "PrefetchHandle", "PrefetchReclaimer"]
