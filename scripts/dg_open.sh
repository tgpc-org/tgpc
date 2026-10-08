#!/usr/bin/env bash
# One-command local dashboard: start + open, no login.
#   ./scripts/dg_open.sh [--port 8765]
set -euo pipefail

PORT=8765
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
mkdir -p data
if curl -sf -o /dev/null "http://127.0.0.1:${PORT}/api/status" 2>/dev/null; then
  echo "dashboard already up on :${PORT}"
else
  nohup python3 scripts/dg_dashboard.py --port "$PORT" > data/dg_dashboard_server.log 2>&1 &
  echo $! > data/dg_dashboard_server.pid
  for _ in $(seq 1 15); do
    sleep 1
    curl -sf -o /dev/null "http://127.0.0.1:${PORT}/api/status" 2>/dev/null && break
  done
  echo "dashboard started on :${PORT} (pid $(cat data/dg_dashboard_server.pid))"
fi
if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:${PORT}/"
else
  echo "open http://127.0.0.1:${PORT}/"
fi
