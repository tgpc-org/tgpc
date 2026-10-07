#!/usr/bin/env bash
# One-command DG fetch launcher (Mac side).
#
#   ./scripts/dg_run.sh [--vm user@host] [--workers N] [--smoke-only] [--restart]
#
# Runs every recurring step in order, each gating the next (fail fast, never
# half-start a fetch): preflight -> drift guards -> input push -> remote env
# -> smoke trial -> loop start -> start verification. Idempotent: safe to
# re-run; refuses instead of double-starting an active loop.
#
# One-time prerequisites (NOT covered here — they need your cloud account
# and choices): a provisioned VM, SSH key auth as $DG_VM, the one-time
# scripts/vps_bootstrap.sh run. See VPS_DG_FETCH.md.
#
#   DG_VM      "user@host" of the fetch VM (required; no default on purpose)
#   DG_WORKERS fetch pool size (default 8, clamped 1..16 — ramp 8->12->16
#              across clean batches, never start at max: a block_storm halt
#              stops everything, which is slower than ramping)
set -euo pipefail

cd "$(dirname "$0")/.."

# Deterministic credentials: ~/.tgpc_env first (background-safe, chmod 600),
# live env wins, Keychain remains the fallback inside the python heredocs.
if [ -f "$HOME/.tgpc_env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$HOME/.tgpc_env"
  set +a
fi

VM="${DG_VM:-}"
WORKERS="${DG_WORKERS:-8}"
SMOKE_ONLY=0
RESTART=0
while [ $# -gt 0 ]; do
  case "$1" in
    --vm=*) VM="${1#--vm=}" ; shift ;;
    --vm) VM="${2:?--vm needs user@host}"; shift 2 ;;
    --workers=*) WORKERS="${1#--workers=}" ; shift ;;
    --workers) WORKERS="${2:?--workers needs 1..16}"; shift 2 ;;
    --smoke-only) SMOKE_ONLY=1 ; shift ;;
    --restart) RESTART=1 ; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1 (see --help)" >&2; exit 2 ;;
  esac
done

if [ -z "$VM" ]; then
  echo "DG_VM is not set. Export it or pass --vm=user@host (SSH key auth required)." >&2
  exit 2
fi
if ! [[ "$WORKERS" =~ ^[0-9]+$ ]] || [ "$WORKERS" -lt 1 ] || [ "$WORKERS" -gt 16 ]; then
  echo "workers must be 1..16 (got $WORKERS)." >&2
  exit 2
fi
if [ "$WORKERS" -gt 12 ]; then
  echo "NOTE: starting above 12 risks a block_storm halt; 8 is the safe start." >&2
fi

SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 $VM"

fail() { echo "ABORT: $1" >&2; exit 1; }

echo "=== [1/7] preflight (local) ==="
[ -f data/rph.json ] || fail "data/rph.json missing — run make scrape first."
[ -f data/dg_fetch_checkpoint.json ] || fail "data/dg_fetch_checkpoint.json missing."
RPH_N=$(python3 -c "import json; print(len(json.load(open('data/rph.json'))))")
CP_DONE=$(python3 -c "import json; print(len(json.load(open('data/dg_fetch_checkpoint.json')).get('completed', [])))")
echo "local rph.json: $RPH_N records; local checkpoint completed: $CP_DONE"
$SSH true 2>/dev/null || fail "cannot SSH to $VM with key auth (set up passwordless login first)."

echo "=== [2/7] drift guards (refuse to push stale state either way) ==="
DB_N=$(python3 -c "
import os
from tgpc.utils import load_credentials
load_credentials()
from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SECRET_KEY'])
n, off = 0, 0
while True:
    rr = sb.table('rph_dg_contacts').select('registration_number').order('registration_number').range(off, off+1999).execute().data or []
    n += len(rr)
    if len(rr) < 2000: break
    off += 2000
print(n)")
echo "Supabase rph_dg_contacts: $DB_N rows"
[ "$DB_N" -le "$CP_DONE" ] || fail "DB ($DB_N) is ahead of local checkpoint ($CP_DONE) — reconcile (cloud->local) before pushing, or the VM will re-probe finished records."
REMOTE_DONE=$($SSH 'python3 -c "import json; print(len(json.load(open(\"tgpc/data/dg_fetch_checkpoint.json\")).get(\"completed\", [])))"' 2>/dev/null || echo 0)
echo "VM checkpoint completed: $REMOTE_DONE"
[ "$REMOTE_DONE" -le "$CP_DONE" ] || fail "VM checkpoint ($REMOTE_DONE) is ahead of local ($CP_DONE) — pull the VM state back before pushing, or its progress is lost."
if $SSH 'sudo -n systemctl is-active --quiet tgpc-dg-fetch' 2>/dev/null; then
  [ "$RESTART" = "1" ] || fail "fetch loop already ACTIVE on the VM — refusing a double start (two writers corrupt the checkpoint). Pass --restart to restart it deliberately."
  echo "loop active, --restart given — will restart after refresh."
fi

echo "=== [3/7] pushing inputs (rph.json + checkpoint) ==="
scp -q data/rph.json "$VM:~/tgpc/data/rph.json"
scp -q data/dg_fetch_checkpoint.json "$VM:~/tgpc/data/dg_fetch_checkpoint.json"
echo "pushed."

echo "=== [4/7] remote env (workers=$WORKERS, warp, disk, OCR) ==="
$SSH "grep -q '^TGPC_DG_WORKERS=' ~/.tgpc_env 2>/dev/null && sed -i 's/^TGPC_DG_WORKERS=.*/TGPC_DG_WORKERS=$WORKERS/' ~/.tgpc_env || echo 'TGPC_DG_WORKERS=$WORKERS' >> ~/.tgpc_env"
$SSH 'command -v warp-cli >/dev/null || echo "WARN: warp-cli missing on VM (GCE IPs are source-blocked without it)."'
$SSH 'command -v tesseract >/dev/null || echo "WARN: tesseract missing on VM (captcha OCR unavailable)."'
$SSH '[ $(df --output=avail ~/tgpc/data | tail -1) -gt 2000000 ] || echo "WARN: <2GB free on VM disk."'
echo "env ok."

echo "=== [5/7] smoke trial (50 records, gates the loop) ==="
SMOKE_OUT=$($SSH 'cd ~/tgpc && ./scripts/vps_fetch.sh --smoke' 2>&1 | tail -15)
echo "$SMOKE_OUT"
echo "$SMOKE_OUT" | grep -qiE "blockederror|block_storm" && fail "smoke hit blocks — investigate (tunnel up? egress changed?) before looping."
SAVED=$(echo "$SMOKE_OUT" | grep -oE '\+[0-9]+ saved' | grep -oE '[0-9]+' | head -1)
[ -n "$SAVED" ] && [ "$SAVED" -gt 0 ] || fail "smoke saved nothing — refusing to start the loop."
echo "smoke passed (+$SAVED saved, no blocks)."
[ "$SMOKE_ONLY" = "1" ] && { echo "smoke-only: stopping here."; exit 0; }

echo "=== [6/7] starting the loop ==="
./scripts/vps_ctl.sh resume "dg_run.sh start (workers=$WORKERS)" >/dev/null
$SSH 'sudo systemctl restart tgpc-dg-fetch'
sleep 20
$SSH 'sudo -n systemctl is-active --quiet tgpc-dg-fetch' || fail "service did not stay up — check: ssh $VM journalctl -u tgpc-dg-fetch -n 50"
echo "loop running."

echo "=== [7/7] next ==="
echo "- Watch: ssh $VM 'journalctl -u tgpc-dg-fetch -f'  or dashboard: ssh -L 8899:localhost:8899 $VM"
echo "- No-SSH ops: ./scripts/vps_ctl.sh status | halt | resume"
echo "- Ramp $WORKERS -> 12 -> 16 across clean batches; halve at the first BlockedError streak."
echo "DONE: fetch loop started (workers=$WORKERS)."
