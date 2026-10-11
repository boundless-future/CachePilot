# Generation/session ledger candidate

2026-10-11 23:05 CST

Implemented `scripts/generation_session_ledger.py` as an opt-in candidate. It is
not imported by `OwnedServiceConnector`, so the production and GPU paths are
unchanged. The ledger separates the current lookup route from every historical
generation and stores acquisition-time wire identities on immutable tickets.

The CPU contract covers two successive preemptions, multiple pending chunks,
late STORE completion after a successor lookup, cancellation, raw-ID reuse,
duplicate terminal events, partial END submission, and the client-wide
sequence/high-water fence. A submitted END is recorded as `submitted`, never as
server reclamation; exceptions remain `unresolved` and the exact generation is
not retried automatically. Lifetime tombstones are bounded by an explicit
capacity and are not silently evicted.

Validation:

* `tests/test_generation_session_ledger.py`: 17 passed
* Existing protocol tests: 15 passed
* Existing preemption identity test: skipped locally because LMCache is only
  installed in the GPU environment

This proves the ledger invariants in isolation. It does not yet connect the
ledger to vLLM's tracker, LazyOffloadManager, MP adapter, or a real server.
The next gate is an adapter integration test that registers generation B after
PREEMPTED without issuing lookup, then verifies B STORE and A/B exact END
ordering against a recording client. Only after that CPU gate should the
candidate be enabled for a single GPU pilot.
