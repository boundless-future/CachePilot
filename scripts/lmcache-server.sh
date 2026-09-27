#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${CACHEPILOT_LOOKUP_SERVER_TIMELINE_DIR:-}" ]]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  SERVER=(python "$PROJECT_ROOT/scripts/lookup_server_timeline.py")
else
  SERVER=(lmcache)
fi
exec "${SERVER[@]}" server --host 127.0.0.1 --port "${LMCACHE_PORT:-5555}" \
  --http-host 127.0.0.1 --http-port "${LMCACHE_HTTP_PORT:-8080}" \
  --l1-size-gb 16 --l1-init-size-gb 16 \
  --eviction-policy LRU --enable-extra-logging "$@"
