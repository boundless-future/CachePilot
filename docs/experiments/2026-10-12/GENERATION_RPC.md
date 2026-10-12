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
