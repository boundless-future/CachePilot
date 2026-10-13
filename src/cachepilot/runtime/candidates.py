"""Candidate ordering. No pins, metadata mutation, or upstream imports."""
from typing import Protocol


class RequestLifecycle(Protocol):
    def is_finished(self, request_id: str) -> bool: ...
    def has_in_flight(self, request_id: str) -> bool: ...


def ranked_candidates(pending, active, lifecycle: RequestLifecycle, protected, ranks):
    """Finished front STOREs ranked by their earliest threatened free block."""
    candidates = []
    for rid, operations in pending.items():
        if (rid in active or not lifecycle.is_finished(rid)
                or lifecycle.has_in_flight(rid) or rid in protected):
            continue
        operation = operations[0]
        positions = [ranks[b] for b in operation.block_hashes if b in ranks]
        if positions:
            candidates.append((min(positions), rid, operation))
    return sorted(candidates)
