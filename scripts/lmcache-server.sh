#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${CACHEPILOT_OWNED_SERVICE_DIR:-}" ]]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
  SERVER=(python "$PROJECT_ROOT/scripts/owned_service.py")
elif [[ -n "${CACHEPILOT_OWNED_MP_DIR:-}" ]]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
  SERVER=(python "$PROJECT_ROOT/scripts/owned_mp_server.py")
elif [[ -n "${CACHEPILOT_LOOKUP_SERVER_RECLAIM:-}" ]]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  SERVER=(python "$PROJECT_ROOT/scripts/lookup_server_reclaim.py")
elif [[ -n "${CACHEPILOT_LOOKUP_SERVER_TIMELINE_DIR:-}" ]]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  SERVER=(python "$PROJECT_ROOT/scripts/lookup_server_timeline.py")
else
  SERVER=(lmcache)
fi
exec "${SERVER[@]}" server --host 127.0.0.1 --port "${LMCACHE_PORT:-5555}" \
  --http-host 127.0.0.1 --http-port "${LMCACHE_HTTP_PORT:-8080}" \
  --l1-size-gb 16 --l1-init-size-gb 16 \
  --eviction-policy LRU --enable-extra-logging "$@"
