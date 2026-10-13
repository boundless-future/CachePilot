"""Pure protection admission decisions, independent of vLLM/LMCache imports.

This policy preserves the existing pessimistic whole-step reserve. It is not
a native single-call demand predictor, and its interface does not make a
smaller reserve safe. Policy changes need separate scheduling validation.
"""
from typing import Protocol


def conservative_reserve(scheduler):
    """Reserve twice EVERY live request's full footprint, including loads."""
    requests = list(scheduler.requests.values())
    if not requests or scheduler.max_num_scheduled_tokens <= 0:
        return None
    if any(getattr(r, "mm_features", None) or getattr(r, "lora_request", None)
           for r in requests):
        return None
    footprint = sum((max(r.num_tokens_with_spec, r.num_computed_tokens) + 1 + 15) // 16
                    for r in requests)
    return 2 * footprint + scheduler.kv_cache_manager.watermark_blocks + 16


class AdmissionPolicy(Protocol):
    def reserve(self, scheduler) -> int | None: ...
    def prefix_blocks(self, candidate_blocks: int, remaining: int) -> int: ...
    def permits(self, free: int, required: int, reserve: int) -> bool: ...


class ConservativeAdmission:
    """Stateless default. Budget counts owned refs, even on shared blocks."""

    def reserve(self, scheduler):
        return conservative_reserve(scheduler)

    def prefix_blocks(self, candidate_blocks, remaining):
        count = min(candidate_blocks, remaining)
        if count < 16 or count % 16:
            raise RuntimeError("Protection budget must contain complete chunks")
        return count

    def permits(self, free, required, reserve):
        return free - required >= reserve
