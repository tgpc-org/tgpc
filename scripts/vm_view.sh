#!/usr/bin/env bash
# Re-open the VM dashboard tunnel on the Mac (sleep kills it; the VM side
# persists under systemd). Usage: ./scripts/vm_view.sh [vm-host]
# Then open http://localhost:8900/
set -euo pipefail

VM_HOST="${1:-${TGPC_VM_HOST:-8.231.107.39}}"
VM_USER="${TGPC_VM_USER:-tgpc-mac}"

pkill -f "8900:localhost" 2>/dev/null || true
sleep 1
ssh -fN -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -L 8900:localhost:8899 "$VM_USER@$VM_HOST"
sleep 2
curl -s -o /dev/null -w "dashboard: http://localhost:8900/ (HTTP %{http_code})\n" \
  --max-time 8 http://localhost:8900/
