# Protected STORE server failure after real CUDA enqueue

This correctness experiment uses one RTX 4090 24GB, Qwen3-4B BF16, vLLM
0.30.0, the fixed LMCache research revision, eager execution, synchronous
scheduling, TP=1, a 2 GiB GPU KV pool, and a two-chunk protection budget. It
is not a performance comparison.

For exactly one protected 512-token STORE, the service first selects the
published wire identity, invokes LMCache's real GPU copy routine, and then
raises a controlled host-side exception. The worker future is not wrapped or
rewritten: it receives the server's original `false` result. The native
terminal is still recorded after the copy submission, and LMCache retires the
transfer with `succeeded=false`. The protection, worker, and scheduler receipts
then all report failure; the original 32 protected pins are released.

The final r3 execution passed: 348 HTTP requests, 14 STORE batches, 9
protection STOREs, 864 retrieved KV layers with 864 bitwise-equal layers, 26
native terminals, 909 final free blocks, and no remaining owned protection
pins. The specialized audit ties the selected batch to
`transfer_submitted -> diagnostic_copy_enqueued -> terminal_seen ->
transfer_retired(succeeded=false)`, plus the unmodified worker result and all
three failed receipts.

LMCache catches this injected exception inside its transfer implementation, so
the outer `OwnedService` `transfer_call_failed` diagnostic is intentionally
not required. `transfer_retired.succeeded=false` is the actual server result
delivered to the worker. This covers a controlled server host exception after
real CUDA enqueue. It does not simulate a CUDA driver fault, an interrupted
DMA/PCIe transaction, or a natural hardware failure.

Evidence: [r3 result](server-store-fault-evidence/result.json),
[configuration](server-store-fault-evidence/config.json), and the retained
handoff/service event logs and KV probe in the same directory. The server log
contains `RuntimeError: Diagnostic host failure after real CUDA enqueue`.

```bash
python scripts/protection_zero_smoke.py \
  --bounded-workload --modes two --server-store-fault \
  --output artifacts/protection-server-store-fault-r3
```
