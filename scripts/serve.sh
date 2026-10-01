#!/usr/bin/env bash
# Foreground vLLM service. Run in the cachepilot Conda environment.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-13.0}"
export PATH="$CUDA_HOME/bin:$PATH"
MODE="${1:-immediate}"
if [[ $# -gt 0 ]]; then shift; fi
case "$MODE" in
  baseline) CONNECTOR_ARGS=() ;;
  immediate) CONFIG=lmcache-0.5.5-retrieve.json ;;
  owned-mp) CONFIG=owned-mp.json ;;
  owned-server-reject) CONFIG=owned-server-reject.json ;;
  fifo) CONFIG=lmcache-0.5.5-smoke.json ;;
  eviction) CONFIG=baseline-kv-transfer.json ;;
  eviction-h5) CONFIG=eviction-h5.json ;;
  decision) CONFIG=decision-trace.json ;;
  adaptive) CONFIG=adaptive-trace.json ;;
  allocation) CONFIG=allocation-signal.json ;;
  allocation-decision) CONFIG=allocation-decision.json ;;
  preallocation-diagnostic) CONFIG=preallocation-diagnostic.json ;;
  lifecycle) CONFIG=lifecycle.json ;;
  lookup-timeline) CONFIG=lookup-timeline.json ;;
  lookup-ordering) CONFIG=lookup-ordering.json ;;
  preemption) CONFIG=preemption.json ;;
  late-store-receipt) CONFIG=late-store-receipt.json ;;
  cancel-held-store) CONFIG=cancel-held-store.json ;;
  preemption-kv-probe) CONFIG=preemption-kv-probe.json ;;
  retrieve-failure) CONFIG=retrieve-failure.json ;;
  server-rejected-retrieve) CONFIG=server-rejected-retrieve.json ;;
  store-failure) CONFIG=store-failure.json ;;
  server-rejected-store) CONFIG=server-rejected-store.json ;;
  *) echo "Usage: $0 {baseline|immediate|fifo|eviction|eviction-h5|decision|adaptive|allocation|allocation-decision|preallocation-diagnostic|lifecycle|lookup-timeline|lookup-ordering|preemption|late-store-receipt|cancel-held-store|preemption-kv-probe|retrieve-failure|server-rejected-retrieve|store-failure|server-rejected-store} [vllm options]" >&2; exit 2 ;;
esac
if [[ "$MODE" != baseline ]]; then
  CONNECTOR_ARGS=(--kv-transfer-config "$(python -c 'import json,os,sys; c=json.load(open(sys.argv[1])); c["kv_connector_extra_config"]["lmcache.mp.port"]=int(os.environ.get("LMCACHE_PORT",5555)); print(json.dumps(c))' "$PROJECT_ROOT/configs/$CONFIG")")
fi
exec vllm serve "${MODEL_PATH:-$PROJECT_ROOT/models/Qwen3-4B}" \
  --host 127.0.0.1 --port "${VLLM_PORT:-8000}" \
  --dtype bfloat16 --max-model-len 8192 --enable-prefix-caching \
  --generation-config vllm --max-num-seqs 4 --shutdown-timeout 10 \
  --gpu-memory-utilization 0.80 \
  --kv-cache-memory-bytes "${KV_CACHE_BYTES:-2147483648}" \
  "${CONNECTOR_ARGS[@]}" "$@"
