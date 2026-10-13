"""CPU research envelope; service admission deliberately disabled.

No resident/hit subtraction. Existing remote-prefill reservations get a
second full-footprint allowance. This is a candidate bound, not a proof for
all native scheduling paths or a replacement for ConservativeAdmission.
"""
from dataclasses import dataclass
from cachepilot.runtime.admission import ConservativeAdmission


@dataclass(frozen=True)
class StepEnvelope:
    request_blocks: tuple[tuple[str, int], ...]
    remote_blocks: int
    watermark_blocks: int
    margin_blocks: int = 16

    @property
    def reserve(self):
        return sum(count for _, count in self.request_blocks) + self.remote_blocks + self.watermark_blocks + self.margin_blocks


def whole_step_envelope(scheduler):
    requests = scheduler.requests
    if not requests or scheduler.max_num_scheduled_tokens <= 0:
        return None
    footprints = {}
    for rid, req in requests.items():
        if (getattr(req, 'mm_features', None) or getattr(req, 'lora_request', None)
                or getattr(req, 'num_in_flight_tokens', 0)
                or getattr(req, 'spec_token_ids', None)):
            return None
        tokens = max(req.num_tokens_with_spec, req.num_computed_tokens)
        if tokens < 0:
            raise ValueError('Negative request footprint')
        footprints[rid] = (tokens + 1 + 15) // 16
    # Read the fixed Scheduler's actual membership; never infer it from a
    # connector lookup flag. Missing membership is unknown, not an empty set.
    inflight = getattr(scheduler, '_inflight_prefills', None)
    if inflight is None:
        return None
    ids = [req.request_id for req in inflight]
    if len(ids) != len(set(ids)) or any(rid not in footprints for rid in ids):
        raise ValueError('Remote prefill membership does not match live requests')
    return StepEnvelope(tuple(sorted(footprints.items())), sum(footprints[rid] for rid in ids),
                        scheduler.kv_cache_manager.watermark_blocks)


class WholeStepAdmission(ConservativeAdmission):
    def reserve(self, scheduler):
        envelope = whole_step_envelope(scheduler)
        return None if envelope is None else envelope.reserve
