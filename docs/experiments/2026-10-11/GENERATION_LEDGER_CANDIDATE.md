# Generation/session ledger candidate

2026-10-11 23:05–23:20 CST

Implemented `scripts/generation_session_ledger.py` as an opt-in candidate. It is
not imported by `OwnedServiceConnector`, so the production and GPU paths are
unchanged. The ledger separates the current lookup route from every historical
generation and stores acquisition-time wire identities on immutable tickets.
`LedgerRequestClient` now exercises the intended adapter boundary: a resumed
tracker may STORE with generation B without a lookup, while END accepts only an
exact wire identity.

The CPU contract covers two successive preemptions, multiple pending chunks,
late STORE completion after a successor lookup, cancellation, raw-ID reuse,
duplicate terminal events, partial END submission, and the client-wide
sequence/high-water fence. A submitted END is recorded as `submitted`, never as
server reclamation; exceptions remain `unresolved` and the exact generation is
not retried automatically. Lifetime tombstones are bounded by an explicit
capacity and are not silently evicted.

Validation:

* Local ledger, boundary and existing protocol tests: **36 passed**.
* Server command below: **39 passed, 66 warnings in 8.32 seconds**. This includes
  17 ledger tests, 4 recording-client boundary tests, 1 new real-library boundary
  replay, 2 original failing-path characterization tests and 15 protocol tests.
  These are CPU contracts in the installed LMCache/vLLM environment, not GPU
  inference experiments. Original failure characterization still passes because
  the original connector path is unchanged.

```sh
python -m pytest -q tests/test_generation_session_ledger.py \
  tests/test_generation_session_ledger_adapter.py \
  tests/test_generation_ledger_native.py \
  tests/test_preempted_tracker_identity.py tests/test_owned_service_protocol.py
```

The native replay invokes actual PREEMPTED tracker recreation, manager pending
admission/drain, STORE receipt and BlockPool reference release. Recording transport
observes LOOKUP A, STORE B, END A, END B. The test explicitly supplies the lookup
terminal, generation producer seals and STORE terminal after manager receipt;
it is not evidence that live adapter callbacks now supply those events correctly.

Boundary review also added a reservation-before-submit STORE API: a ticket
reserved under A may submit after B appears without consulting current raw-ID
state. Raw-ID END is rejected, not delegated to the underlying client. Unknown
transport outcomes keep the ticket active rather than claiming completion.

The candidate is not a drop-in RequestClient: it intentionally does not implement
automatic future polling, incarnation registration/heartbeat, status/free routing
or scheduler/worker ticket transport. Live integration must reserve STORE at
manager admission, register all reset/reuse generations, resolve tickets only
from trusted callbacks and supply a producer barrier independent of raw-ID finish.
The high-water rule is deliberately conservative and may delay END behind other
unsealed older generations. Lifetime generation tombstones are capped, but
per-generation ticket retention still needs an explicit resource budget before
long-running use.

Next: wire those actual callbacks in a separate opt-in connector, cover old
in-flight STORE during reset and raw-ID reuse with the real manager, audit server
sessions by exact wire identity, then run one GPU pilot. No inference service was
started during this change; long-prefill formal remains blocked.
