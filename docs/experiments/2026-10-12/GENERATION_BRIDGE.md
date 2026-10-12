# Generation callback bridge — 2026-10-12

## Implementation gate

Added a separately selected `GenerationLedgerConnector`; the explicit
`cachepilot.generation_ledger=true` opt-in is mandatory. Only the existing fixed
single-worker synchronous text-only zero-protection stack is allowed. Original
idle identity guard, owned service, and default protection connector are unchanged.

The facade over the real LazyOffloadManager reserves a ticket at STORE admission,
accounts native pending drops, and carries acquisition wire identity through
coalescing and original worker batch receipts. The native manager still owns and
frees BlockPool pins. A complete versioned receipt is checked before any native
receipt mutation. Old orphan failure never addresses the new pending generation.

The scheduler registers every real tracker, including PREEMPTED generations that
skip LOOKUP. Current STORE identity and original LOOKUP route stay separate.
Native session-release actions provide the producer barrier after manager closure;
RETRIEVE completion and retained lookup/status/free futures must settle before
exact-wire END submission. Submission is not server ACK or resource reclamation.

An owner-thread input-queue wrapper polls outstanding RPC futures while the core
is idle. It preserves arrivals and the inherited idle grace without running fake
model/scheduler work. Shutdown records the ledger and rejects unresolved ownership.
Lifetime generation/operation limits remain bounded and fail closed.

## Evidence so far (13:00–13:09 CST)

Real native callback/manager suites: **16 passed, 66 warnings, 7.79 seconds**.
Includes one/two PREEMPTED tracker rebuilds, cancellation before lookup ACK,
preserved idle guard, real raw-ID reuse with old in-flight success/failure,
duplicate/stale receipts, coalesced pending chunks, and queue-pump ordering.
Transport completion is controlled; this is not real RPC or GPU service evidence.

Full server regression with explicit CUDA/native opt-ins:
**805 passed, 115 subtests, 121 warnings, 19.40 seconds**.
Saved outputs: [targeted](generation-bridge-evidence/generation-bridge-targeted-20261012.txt)
and [full](generation-bridge-evidence/generation-bridge-full-20261012.txt).
The independently added pilot auditor passed 9 local adversarial checks after
this full snapshot; it is not included in the 805 count.

## Independent pilot gate

`run_generation_pilot.py` preserves the original failed long-prefill trace
(`d15108f305834554a7cd3cbe7da5103ee3a8348e587594300db5bf37d29e3cd0`),
Qwen3-4B/BF16, 2 GiB GPU KV pool, h2.5/d64 and 15-second idle grace.
The only functional candidate change is the opt-in generation bridge. The
read-only server observer adds exact-wire END-return and session/job snapshots;
original service reclamation remains authoritative. Handoff logs retain the
same original batch IDs across scheduler/worker phases.

Pass requires original replay/token/resource gates, independent timestamp audit,
one END submission and server handler return per registered wire, every observed
LOOKUP/transfer wire registered, all original batches closed, ledger operations
terminal/dropped, and exact empty server snapshots before/after engine stop.
Logging is enabled, so this is diagnostic rather than formal performance data.

Original pilot and two failed GPU diagnostics remain failed. No long-prefill
formal run is authorized by CPU tests alone; a fresh pilot must pass first.
