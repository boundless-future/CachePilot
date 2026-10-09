"""Zero-budget handoff accounting; observes default STORE, owns no GPU pins.

Identity is captured at metadata construction and carried to the worker and
back. A scheduler receipt is not a native terminal: the service's independent
terminal audit remains mandatory. Only one worker and one batch per raw ID.
"""
from dataclasses import dataclass
import hashlib
import json

from scripts.owned_service_protocol import wire_id


@dataclass(frozen=True)
class ProtectionConfig:
    enabled: bool = False
    max_chunks: int = 0

    @classmethod
    def parse(cls, extra):
        enabled = extra.get("cachepilot.protection.enabled", False)
        budget = extra.get("cachepilot.protection.max_chunks", 0)
        if type(enabled) is not bool or type(budget) is not int or budget < 0:
            raise ValueError("Protection requires boolean enabled and integer chunk budget")
        if budget != 0:
            raise ValueError("GPU protection currently supports zero budget only")
        return cls(enabled, budget)


def describe(meta):
    op = meta.op
    return dict(request_id=meta.request_id, wire_id=wire_id(meta),
        direction=meta.direction, start=op.start, end=op.end,
        skip=op.skip_first_n_tokens, block_ids=[list(g) for g in op.block_ids],
        prefix_sha256=hashlib.sha256(json.dumps(
            [meta.cache_salt, list(op.token_ids[:op.end])], separators=(",", ":")).encode()).hexdigest())


class HandoffLedger:
    def __init__(self, record):
        self.record = record
        self.pending = {}

    def accept(self, batch, phase):
        rid = batch["request_id"]
        if rid in self.pending:
            raise RuntimeError("Overlapping STORE across a raw request ID")
        self.pending[rid] = dict(batch)
        self.record(phase, **batch)

    def verify(self, completed, failed, identities=None):
        if not set(failed) <= set(completed):
            raise ValueError("Failure without a completion count")
        if identities is not None and set(identities) != set(completed):
            raise ValueError("Receipt identity set differs from completed requests")
        batches = {}
        for rid, count in completed.items():
            if type(count) is not int or count != 1:
                raise ValueError("Only a single-worker terminal receipt is supported")
            batch = self.pending.get(rid)
            if batch is None:
                raise ValueError("Receipt has no original STORE handoff")
            if identities is not None and identities[rid] != batch["batch_id"]:
                raise ValueError("Receipt addresses a stale STORE generation")
            batches[rid] = batch
        return batches

    def retire(self, batches, failed, phase):
        for rid, batch in batches.items():
            if self.pending.get(rid) != batch:
                raise RuntimeError("Handoff changed during receipt processing")
            self.record(phase, **batch, failed=rid in failed)
            del self.pending[rid]
