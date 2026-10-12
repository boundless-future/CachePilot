# Generation stack / decode192 references

Preregistered 2026-10-12 13:47 CST, before decode192 GPU cells. The preceding
long-prefill formal matrix is still finishing; it must pass its complete
independent audit before this stage starts. No selection based on its rankings.

Freeze Qwen3-4B BF16, RTX 4090 24 GB, 2 GiB GPU KV, eager synchronous single
worker. Transform only output budgets (48 to 192) of the original held-out
reference SHA `015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`.
Preserve prompts, arrivals, sessions, think times and seeds. New trace SHA:
`5deebe7cd1b7ac052ef0010fd870f349f7058675db93d9df20c5bc5f8d9c8cae`.
68 requests, 75,808 prompt tokens, 13,056 output tokens; maximum prompt plus
output is 1,380 tokens, below the unchanged 8,192 context bound. Generated text
does not feed later turns. Fixed seeds/budgets do not guarantee the same decode
text, scheduling trajectory or sustained concurrency.

Compare the same four whole configurations: native, original owned-immediate,
generation-ledger zero-protection h2.5/d64, and generation-ledger zero-protection
h5.0/d1. The latter remains selected from the original development tuning.
Preserve the 15-second idle grace, original identity guard and zero protection.
This is a workload extension, not new protection-policy validation or retuning.

Freeze scripts/configs after runner sync and appropriate regression. A new
four-cell pilot with independent audit is mandatory, then three formal repeats
in the existing rotated order. All source/config/token/time/error/H2D/resource
gates remain. Exact server session/job/transfer sets must empty before and after
engine stop; lazy generation summaries must close all operations. No hot-path
diagnostic JSONL; shutdown-only evidence remains enabled. A failure stops the
matrix and retains failed/unrun cells; incomplete matrices have no mean/ranking.

Primary reported metrics: TTFT P95, ready-to-end P95, session completion,
computed prefill and GPU/external hit tokens, queue and preemption counters,
H2D/D2H staging bytes. Report three individual values and mean/sample SD.
Secondary observed streaming duration is first-to-last nonempty SSE fragment,
with duration/(output tokens minus one) and last-fragment-to-response gap.
Fragments can contain multiple tokens; these are not per-token ITL or kernel
timings. Request duration and labeled staging elapsed counters are descriptive,
can overlap, and must not be added into an exclusive latency budget. Phase
decomposition is post hoc. Preserve all output-hash mismatches.

Stop starting GPU cells at 14:55 CST (UTC 06:55) to reserve finalization time
for the user's 15:00 report. Do not increase concurrency or skip pilot to fit
the deadline. Preserve a completed pilot if formal cannot finish.
