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

# Deterministic credentials: $VM_HOME/.tgpc_env first (background-safe, chmod 600),
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
RETRY_TERMINAL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --vm=*) VM="${1#--vm=}" ; shift ;;
    --vm) VM="${2:?--vm needs user@host}"; shift 2 ;;
    --workers=*) WORKERS="${1#--workers=}" ; shift ;;
    --workers) WORKERS="${2:?--workers needs 1..16}"; shift 2 ;;
    --smoke-only) SMOKE_ONLY=1 ; shift ;;
    --restart) RESTART=1 ; shift ;;
    --retry-terminal) RETRY_TERMINAL=1 ; shift ;;
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

# Plain ssh fails where only OS Login certs work (this project's GCE VMs).
# When gcloud can resolve the VM by its static IP, route everything through
# `gcloud compute ssh/scp`, which mints certs transparently. Otherwise fall
# back to direct ssh/scp (other providers).
GCE_INST=""
GCE_ZONE=""
# Fetch runs as this user (owns the checkout the systemd unit points at).
VM_USER="tgpc-mac"
VM_HOME="/home/tgpc-mac"
if command -v gcloud >/dev/null 2>&1; then
  _HOST="${VM##*@}"
  _FOUND=$(gcloud compute instances list --filter="networkInterfaces.accessConfigs.natIP='${_HOST}'" --format='value(name,zone)' 2>/dev/null || true)
  GCE_INST=$(echo "$_FOUND" | awk '{print $1}')
  GCE_ZONE=$(echo "$_FOUND" | awk '{print $2}' | awk -F/ '{print $NF}')
  [ -n "$GCE_INST" ] && [ -n "$GCE_ZONE" ] && echo "SSH via gcloud (OS Login certs): $GCE_INST @ $GCE_ZONE"
fi

gssh() {
  if [ -n "$GCE_INST" ]; then
    # --tunnel-through-iap: a live WARP tunnel blackholes direct inbound,
    # IAP goes through Google's backbone and always reaches the VM.
    gcloud compute ssh "$GCE_INST" --zone="$GCE_ZONE" --tunnel-through-iap --ssh-flag="-o ConnectTimeout=15" --command "$*"
  else
    # shellcheck disable=SC2086
    $SSH $*
  fi
}

gscp_to() { # gscp_to <local> <remote-path-under-VM_HOME>
  local dest="$VM_HOME/$2"
  if [ -n "$GCE_INST" ]; then
    gcloud compute scp --zone="$GCE_ZONE" --tunnel-through-iap -q "$1" "$GCE_INST:/tmp/dg_push_$(basename "$1")"
    gssh "sudo install -o $VM_USER -g $VM_USER -m 644 /tmp/dg_push_$(basename "$1") '$dest'"
  else
    scp -q "$1" "$VM:$dest"
  fi
}

# Run a remote command as the fetch user (owns checkout + data dir), starting
# inside the checkout so relative data/ paths resolve.
gfetch() {
  gssh "sudo -n -u $VM_USER VM_HOME=$VM_HOME bash -lc 'cd \$VM_HOME/tgpc && $*'"
}

fail() { echo "ABORT: $1" >&2; exit 1; }

echo "=== [1/7] preflight (local) ==="
[ -f data/rph.json ] || fail "data/rph.json missing — run make scrape first."
[ -f data/dg_fetch_checkpoint.json ] || fail "data/dg_fetch_checkpoint.json missing."
RPH_N=$(python3 -c "import json; print(len(json.load(open('data/rph.json'))))")
CP_DONE=$(python3 -c "import json; print(len(json.load(open('data/dg_fetch_checkpoint.json')).get('completed', [])))")
echo "local rph.json: $RPH_N records; local checkpoint completed: $CP_DONE"
gssh true 2>/dev/null || fail "cannot SSH to $VM (gcloud OS Login certs or key auth required)."

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
REMOTE_DONE=$(gfetch 'python3 -c "import json; print(len(json.load(open(\"tgpc/data/dg_fetch_checkpoint.json\")).get(\"completed\", [])))"' 2>/dev/null || echo 0)
echo "VM checkpoint completed: $REMOTE_DONE"
[ "$REMOTE_DONE" -le "$CP_DONE" ] || fail "VM checkpoint ($REMOTE_DONE) is ahead of local ($CP_DONE) — pull the VM state back before pushing, or its progress is lost."
if gssh 'sudo -n systemctl is-active --quiet tgpc-dg-fetch' 2>/dev/null; then
  [ "$RESTART" = "1" ] || fail "fetch loop already ACTIVE on the VM — refusing a double start (two writers corrupt the checkpoint). Pass --restart to restart it deliberately."
  echo "loop active, --restart given — will restart after refresh."
fi

echo "=== [3/7] pushing inputs (code + rph.json + checkpoint) ==="
gfetch 'cd $VM_HOME/tgpc && git pull -q 2>&1 | tail -1 || echo "WARN: git pull failed — loop runs last-pulled code"'
gscp_to data/rph.json tgpc/data/rph.json
gscp_to data/dg_fetch_checkpoint.json tgpc/data/dg_fetch_checkpoint.json
echo "pushed."

echo "=== [4/7] remote env (workers=$WORKERS, retry-terminal=$RETRY_TERMINAL, warp, disk, OCR) ==="
gfetch "grep -q '^TGPC_DG_WORKERS=' \$VM_HOME/.tgpc_env 2>/dev/null && sed -i 's/^TGPC_DG_WORKERS=.*/TGPC_DG_WORKERS=$WORKERS/' \$VM_HOME/.tgpc_env || echo 'TGPC_DG_WORKERS=$WORKERS' >> \$VM_HOME/.tgpc_env"
gfetch "grep -q '^TGPC_DG_RETRY_TERMINAL=' \$VM_HOME/.tgpc_env 2>/dev/null && sed -i 's/^TGPC_DG_RETRY_TERMINAL=.*/TGPC_DG_RETRY_TERMINAL=$RETRY_TERMINAL/' \$VM_HOME/.tgpc_env || echo 'TGPC_DG_RETRY_TERMINAL=$RETRY_TERMINAL' >> \$VM_HOME/.tgpc_env"
gssh 'command -v warp-cli >/dev/null || echo "WARN: warp-cli missing on VM (GCE IPs are source-blocked without it)."'
gssh 'command -v tesseract >/dev/null || echo "WARN: tesseract missing on VM (captcha OCR unavailable)."'
gssh '[ $(df --output=avail $VM_HOME/tgpc/data | tail -1) -gt 2000000 ] || echo "WARN: <2GB free on VM disk."'
echo "env ok."

echo "=== [5/7] smoke trial (50 records, gates the loop) ==="
SMOKE_OUT=$(gfetch 'cd $VM_HOME/tgpc && ./scripts/vps_fetch.sh --smoke' 2>&1 | tail -15)
echo "$SMOKE_OUT"
echo "$SMOKE_OUT" | grep -qiE "blockederror|block_storm" && fail "smoke hit blocks — investigate (tunnel up? egress changed?) before looping."
SAVED=$(echo "$SMOKE_OUT" | grep -oE '\+[0-9]+ saved' | grep -oE '[0-9]+' | head -1)
if [ -z "$SAVED" ] || [ "$SAVED" -le 0 ]; then
  if [ "$RETRY_TERMINAL" = "1" ]; then
    echo "WARN: smoke saved nothing — expected on refused ranges; blocks check still gates."
  else
    fail "smoke saved nothing — refusing to start the loop."
  fi
fi
echo "smoke passed (+${SAVED:-0} saved, no blocks)."
[ "$SMOKE_ONLY" = "1" ] && { echo "smoke-only: stopping here."; exit 0; }

echo "=== [6/7] starting the loop ==="
./scripts/vps_ctl.sh resume "dg_run.sh start (workers=$WORKERS)" >/dev/null
gssh 'sudo systemctl restart tgpc-dg-fetch'
sleep 20
gssh 'sudo -n systemctl is-active --quiet tgpc-dg-fetch' || fail "service did not stay up — check: ssh $VM journalctl -u tgpc-dg-fetch -n 50"
echo "loop running."

echo "=== [7/7] next ==="
echo "- Watch: ssh $VM 'journalctl -u tgpc-dg-fetch -f'  or dashboard: ssh -L 8899:localhost:8899 $VM"
echo "- No-SSH ops: ./scripts/vps_ctl.sh status | halt | resume"
echo "- Ramp $WORKERS -> 12 -> 16 across clean batches; halve at the first BlockedError streak."
echo "DONE: fetch loop started (workers=$WORKERS)."
