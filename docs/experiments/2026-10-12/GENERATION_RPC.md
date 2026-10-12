# Generation RPC candidate — 2026-10-12

Continued the ledger work after the user reported server startup. Two SSH
attempts to the previously configured endpoint timed out; no remote environment
inspection, server tests or inference experiment completed this turn.

## Implemented

`scripts/generation_rpc_client.py` adds an independent scheduler-thread client
candidate, not installed by any connector. Lookup registers the incarnation via
ping. Lookup/status/free acquire exact-wire operation tickets before transport
submission. The original futures are returned to callers; nonblocking polling
marks tickets terminal only after successful future observation. Submission or
result errors retain unresolved active ownership without retries. STORE and
RETRIEVE are not declared terminal from RPC replies.

Status cannot precede observed lookup acknowledgement. Tracker registration
without lookup preserves the previous lookup route. An old status completion
after a new lookup changes only its captured ticket. Cleanup metadata selects
its acquisition identity rather than the current raw-ID route.

The ledger now also limits total lifetime operations (default 65,536). Completed
ticket tombstones still count. Exhaustion rejects new operations before sending
an RPC; it does not evict identity evidence or imply automatic recovery.

## Validation

```sh
python -m pytest -q tests/test_generation_rpc_client.py \
  tests/test_generation_session_ledger.py \
  tests/test_generation_session_ledger_adapter.py \
  tests/test_owned_service_protocol.py
```

Local result: **49 passed** (13 new RPC/capacity cases plus 36 existing cases).
Transport uses recording doubles, concurrent futures and a query/result double.
This does not verify installed MessagingFuture semantics or actual RPC ordering.
The previous server's 39-test result and full CUDA regression are historical,
not rerun results for this revision.

## Remaining integration gates

1. Restore SSH access; verify pinned installed sources and actual futures.
2. Wire scheduler polling and producer barriers in a separate opt-in connector;
   the generic client is not a drop-in production RequestClient.
3. Bind manager admission/reset/reuse and cross-process STORE receipts to tickets,
   cover real old in-flight batches and late receipt isolation.
4. Validate server session closure by exact wire, then run an isolated GPU pilot.

No default path or idle guard changed. Long-prefill formal remains blocked.

## Connection restored and server verification (12:48–12:51 CST)

The original SSH endpoint became reachable. RTX 4090 was idle (1 MiB, 0%).
Latest candidate sources were copied to the existing project; the six-file
server suite passed **52 tests, 66 warnings in 16.52 seconds**. A separate new
installed MessagingFuture contract passed **3 tests, 3 warnings in 1.65 seconds**:
pending prevents END, successful None/False results terminate the RPC ticket,
and an exception retains unresolved ownership. These controlled future results
are not real RPC delivery or STORE/RETRIEVE completion evidence.

Saved output: [targeted](generation-rpc-evidence/targeted.txt) and
[native future](generation-rpc-evidence/native-future.txt). The new test is
`tests/test_generation_rpc_native.py`. No inference experiment was started.

Installed source SHA256 checks:

* MP adapter: `63fe75512185c17e990c49c172d2a381741cc6cbc0eb6f8c534cc9ff5df2912c`
* Lazy manager: `e611da21991cd29757e553acfaeabbdd1332d4743f036f6b35ed0e85c80c5bcd`
* Multiprocess futures: `97cd68ed22b8bfd50aea9f89df53ae51a3f1776c4d02cc40ed4d870ff34b941b`

Adapter/manager signatures match the pinned connector requirements. Connectivity
is no longer blocking; actual callback integration and exact-wire service audits
remain the next gates. The 52+3 focused checks are not a full CUDA regression.
