# New generation stack / long-prefill references

Preregistered 2026-10-12 13:20 CST, before new performance cells.

The diagnostic c passes original trace/token/error gates, exact-wire END and
server drain, original reservation/terminal audit and actual BlockPool restore.
Its timings remain excluded because hot-path event logging was enabled.

Freeze Qwen3-4B BF16, RTX 4090 24 GB, 2 GiB GPU KV capacity, synchronous eager
single-worker text-only execution, original long-prefill trace SHA
`d15108f305834554a7cd3cbe7da5103ee3a8348e587594300db5bf37d29e3cd0`,
unchanged arrival times and decode budgets. Preserve 15-second idle grace and
original guard. No tuning on the long-prefill results.

Four complete configurations:

1. Native vLLM, no MP service or KV connector.
2. Existing owned-immediate connector, lazy offload disabled.
3. GenerationLedgerConnector, zero protection, default h2.5/d64.
4. Same generation stack, zero protection, previously selected h5.0/d1.

Original development selection is independently reconstructed from all three
repetitions of all six fixed candidates; selected parameters must remain h5.0/d1.
This compares entire configurations. Immediate uses the original ownership
connector; changes in lifecycle/connector cannot be attributed to policy alone.

Disable handoff and ownership request-path JSONL. Retain existing one-time
shutdown record, a new one-time scheduler generation-summary JSON, and read-only
exact server status snapshots. The summary adds no lifecycle decisions; submitted
END still does not mean server ACK. Original service drain remains authoritative.

Run a separate four-cell pilot. Require each cell's token count, timestamps,
source/config/capacity signature, fatal-log and resource gates; caching groups
must actually use H2D. Lazy groups additionally require every generation sealed,
all operations terminal/dropped and no pending RPC/store/retrieve/deferred END.
All MP groups require exact empty server sets before/after engine stop.
No per-request JSONL is allowed. Independently recompute the whole pilot.

Only complete pilot success permits three formal repetitions (12 cells),
rotating start order by three positions each repetition. Sources remain frozen
through pilot/formal. Any failed gate stops the matrix, retaining failures and
unrun cells; no partial performance mean. Report TTFT/ready-to-first/ready-to-end,
session latency, prefill/GPU/external hits, queue and H2D/D2H, all per-repeat
values and output-hash mismatches. Fixed decode length does not imply identical
generated tokens. No protection-strategy benefit can be inferred from this matrix.

Runner: `scripts/run_generation_matrix.py`. Deadline 15:00 CST: stop starting
new cells; let current bounded work finish safely and retain incomplete status.
