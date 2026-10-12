# Next workload: longer sustained decode

Design recorded 2026-10-12, while the long-prefill matrix runs. This is a planned
follow-up, not an executed experiment or a claim of stable decode performance.

Use the original independently held-out reference trace (SHA
`015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`),
with its 68 prompts, sessions, planned arrivals, seeds and think-time fields
unchanged. Change every fixed output budget from 48 to 192 tokens, with the
existing `ignore_eos=true`. Do not append generated tokens to later prompts;
open-loop replay is a serving experiment rather than a live causal Agent.

The new offered work is 13,056 output tokens instead of 3,264. Its purpose is to
lengthen the concurrent decode phase and test whether the conclusions from the
short-decode baseline change under a larger decode budget. It does not guarantee
a stable number of concurrent requests, identical decoded text or identical
preemption trajectories. Check unchanged 8K context limits before starting.

Retain the four unprotected complete configurations and h5.0/d1 selection from
the generation long-prefill comparison. Prerequisite: complete long-prefill
pilot/formal audit, or an explicitly recorded performance limitation rather
than silently skipping a correctness failure. Freeze new sources/trace after
extending the runner; run a fresh four-cell pilot before any formal repetitions.

Use the existing full token, source/config, raw timing, fatal error, H2D, exact
server drain and shutdown generation gates. Record TTFT plus ready-to-end and
session completion; also report the average elapsed time from first token to
last nonempty SSE fragment. This last measure is observed streaming duration,
not per-token GPU kernel latency, because a fragment may contain several tokens.
Report prefill/GPU/external hits, queue, preemptions and transfer bytes to explain
latency changes. No partial matrix averages or cross-stack subtraction.

If the clock does not leave enough time, retain a complete smaller stage and
resume the next stage later; do not increase concurrency to fit the deadline.
