#!/usr/bin/env bash
# Long-run DG fetch harness for the VPS. Loops in BATCH_SIZE chunks until
# the ID pool is exhausted, syncing to cloud + backing up the checkpoint
# after every batch.
#
# Usage:
#   ./scripts/vps_fetch.sh [--smoke] [--batch N]
#     --smoke   50-record trial (proves WARP egress + creds before a long run)
#     --batch N records per loop iteration (default 1000, smoke forces 50)
#
# Stop cleanly:  touch data/dg_halt   (halts after the current batch)
# Stop now:      sudo systemctl stop tgpc-dg-fetch   (in-flight record retries next run)
# Remote (no SSH): scripts/vps_ctl.sh halt|resume|status (R2 ops/ctl.json channel)
# Dashboard:     ssh -L 8899:localhost:8899 <vm>  then open http://localhost:8899/
set -euo pipefail

SMOKE=0
BATCH="${TGPC_BATCH_SIZE:-1000}"
while [ $# -gt 0 ]; do
  case "$1" in
    --smoke) SMOKE=1; BATCH=50; shift ;;
    --batch) [ $# -ge 2 ] || { echo "--batch needs a value" >&2; exit 2; }; BATCH="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."  # repo root (~/tgpc on the VM)
if [ -f "$HOME/.tgpc_env" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.tgpc_env"
else
  echo "FATAL: ~/.tgpc_env missing — run scripts/vps_bootstrap.sh first" >&2
  exit 1
fi
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi
for var in SUPABASE_URL SUPABASE_SECRET_KEY R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY CLOUDFLARE_ACCOUNT_ID TGPC_R2_DG_BUCKET; do
  if [ -z "${!var:-}" ]; then echo "FATAL: $var not set in ~/.tgpc_env" >&2; exit 1; fi
done
mkdir -p data

# --- WARP: connect explicitly per run, never autostart ------------------------
# GCE IPs are source-blocked (proven 2026-09-30: 0/20 direct, ConnectTimeout),
# so the tunnel is mandatory — but a headless box manages it deliberately:
# warp-svc autostart once blackholed our SSH. We connect here, exclude
# operator IPs so SSH survives, and disconnect on exit (trap) so the box is
# always reachable between runs.
CTL_SSH_EXCLUDE=""
apply_exclusions() {
  # $1 = space-separated CIDRs kept reachable through an active tunnel.
  # 2026.x syntax: `tunnel ip add-range` (bare `add-excluded-route` is gone).
  for net in $1; do
    warp-cli --accept-tos tunnel ip add-range "$net" 2>/dev/null \
      || warp-cli --accept-tos tunnel ip add "${net%%/*}" 2>/dev/null \
      || echo "WARNING: could not exclude $net from WARP — SSH may drop while tunnel is up" >&2
  done
}
warp_up() {
  warp-cli --accept-tos connect
  sleep 5
  apply_exclusions "${TGPC_SSH_EXCLUDE:-}"
  CTL_SSH_EXCLUDE="${TGPC_SSH_EXCLUDE:-}"
  warp-cli --accept-tos status 2>/dev/null | head -2 || true
  echo "egress now: $(curl -s --max-time 10 https://icanhazip.com)"
}
poll_ctl() {
  # R2 operator channel (ops/ctl.json): halt flag + live SSH exclusions.
  # Returns 1 = pause this round (HALT set), 0 = fetch on. Any read failure
  # fails OPEN (a broken control plane must never stop fetching).
  # NOTE: heredoc writes a temp FILE (not heredoc-in-$(), which macOS bash
  # 3.2 cannot parse) before capturing output.
  cat > /tmp/vps_poll_ctl.py <<'PYEOF'
import sys, json, boto3, os
bucket = os.environ["TGPC_R2_DG_BUCKET"]
try:
    s3 = boto3.client("s3", endpoint_url=f"https://{os.environ['CLOUDFLARE_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                      aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])
    s3.download_file(bucket, "ops/ctl.json", "/tmp/vps_ctl.json")
    ctl = json.load(open("/tmp/vps_ctl.json"))
    print(f"HALT={str(bool(ctl.get('halt'))).upper()}")
    print(f"EXCL={ctl.get('ssh_exclude', '')}")
except Exception:
    print("HALT=FALSE")
    print("EXCL=")
PYEOF
  local out
  out=$(python3 /tmp/vps_poll_ctl.py 2>/dev/null) || return 0
  local halt excl
  halt=$(echo "$out" | grep ^HALT= | cut -d= -f2)
  excl=$(echo "$out" | grep ^EXCL= | cut -d= -f2-)
  if [ -n "$excl" ] && [ "$excl" != "$CTL_SSH_EXCLUDE" ]; then
    echo "ctl: updating SSH exclusions: $excl"
    apply_exclusions "$excl"
    CTL_SSH_EXCLUDE="$excl"
  fi
  [ "$halt" = "TRUE" ] && return 1 || return 0
}
warp_down() {
  warp-cli --accept-tos disconnect 2>/dev/null || true
}
if ! command -v warp-cli >/dev/null 2>&1; then
  echo "FATAL: warp-cli missing — GCE IPs are source-blocked. Run vps_bootstrap.sh." >&2
  exit 1
fi
warp_up
trap warp_down EXIT

# --- reference data -----------------------------------------------------------
if [ ! -f data/rph.json ]; then
  echo "downloading data/rph.json (serial-order reference)…"
  curl -fsSL "$SUPABASE_URL/storage/v1/object/tgpc/rph.json" -o data/rph.json
fi

# --- checkpoint restore (fresh VM only; local file always wins) ---------------
if [ ! -f data/dg_fetch_checkpoint.json ]; then
  echo "no local checkpoint — trying R2 ops backup…"
  python3 - "$TGPC_R2_DG_BUCKET" <<'PYEOF' || echo "(no R2 checkpoint backup found — starting fresh)"
import sys, boto3, os
bucket = sys.argv[1]
s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{os.environ['CLOUDFLARE_ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
)
for name in ("data/dg_fetch_checkpoint.json", "data/dg_stats.json"):
    try:
        s3.download_file(bucket, f"ops/{name.split('/')[-1]}", name)
        print("restored", name)
    except Exception as e:
        print("skip", name, e)
        sys.exit(1)
PYEOF
fi

backup_checkpoint() {
  # Best-effort: a failed backup must NEVER kill the fetch loop (progress is
  # already cloud-synced via --sync-cloud and the local checkpoint persists).
  # Checkpoint holds reg IDs + failure reasons (no PII) but lives in the
  # private DG bucket under ops/ anyway — never the public bucket.
  python3 - "$TGPC_R2_DG_BUCKET" <<'PYEOF' || echo "WARNING: R2 checkpoint backup failed — local checkpoint intact, will retry next batch" >&2
import sys, boto3, os
bucket = sys.argv[1]
s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{os.environ['CLOUDFLARE_ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
)
for name in ("dg_fetch_checkpoint.json", "dg_stats.json"):
    local = f"data/{name}"
    if os.path.exists(local):
        s3.upload_file(local, bucket, f"ops/{name}")
        print("backed up", name)
PYEOF
}

gen_ids() {
  # Same ordering as the local dashboard (scripts/dg_dashboard.py::next_ids):
  # retryable failures first, then fresh — both in rph.json serial order.
  python3 - "$1" <<'PYEOF'
import json, sys
count = int(sys.argv[1])
rows = json.load(open("data/rph.json"))
order = {r["registration_number"]: (r.get("serial_number") is None, r.get("serial_number") or 0)
         for r in rows if r.get("registration_number")}
try:
    cp = json.load(open("data/dg_fetch_checkpoint.json"))
except FileNotFoundError:
    cp = {}
done = set(cp.get("completed", [])) | set(cp.get("failed_terminal", {}))
retryable = sorted([r for r in cp.get("failed", {}) if r not in done],
                   key=lambda r: order.get(r, (True, 0)))
seen = set(retryable)
fresh = sorted([reg for reg in order if reg not in done and reg not in seen],
               key=lambda r: order.get(r, (True, 0)))
ids = (retryable + fresh)[: max(0, count)]
open("data/dg_ids_vps.txt", "w").write("\n".join(ids) + ("\n" if ids else ""))
print(f"ids: {len(ids)} ({len([i for i in ids if i in set(retryable)])} retryable)")
PYEOF
}

# --- main loop ----------------------------------------------------------------
if [ "$SMOKE" = "1" ]; then
  echo "=== SMOKE: 50 records via WARP tunnel ==="
  gen_ids 50
  [ -s data/dg_ids_vps.txt ] || { echo "nothing to fetch"; exit 0; }
  before=$(python3 -c "import json;s=json.load(open('data/dg_stats.json'));print(str(s.get('done',0))+' '+str(s.get('failed',0)))" 2>/dev/null || echo "0 0")
  python3 -m tgpc fetch-dg --ids-file data/dg_ids_vps.txt \
    --sync-cloud --sync-every 50
  backup_checkpoint
  python3 -c "
import json
s = json.load(open('data/dg_stats.json'))
b_done, b_failed = map(int, '$before'.split())
d_done = s.get('done', 0) - b_done
d_failed = s.get('failed', 0) - b_failed
print(f'smoke delta: +{d_done} saved, +{d_failed} failed | reasons:', s.get('fail_by_reason'))"
  echo "=== SMOKE DONE — go only if saved delta > 0 with no BlockedError streak ==="
  exit 0
fi

while true; do
  if [ -f data/dg_halt ]; then echo "dg_halt present — stopping cleanly"; break; fi
  # Multi-day runs: re-verify the tunnel every batch. If it dropped, fetches
  # would silently go direct (datacenter IP = blocks), so reconnect first.
  if ! warp-cli --accept-tos status 2>/dev/null | grep -qi "connected"; then
    echo "tunnel down mid-run — reconnecting…"
    warp_up
  fi
  # R2 operator channel: remote halt/resume + live exclusion updates, no SSH.
  if ! poll_ctl; then
    echo "ctl: HALT set — pausing 5 min (clear with scripts/vps_ctl.sh resume)"
    sleep 300
    continue
  fi
  gen_ids "$BATCH"
  [ -s data/dg_ids_vps.txt ] || { echo "ID pool exhausted — all done"; break; }
  python3 -m tgpc fetch-dg --ids-file data/dg_ids_vps.txt \
    --sync-cloud --sync-every 50
  backup_checkpoint
  # data/dg_stop (or the R2 halt flag via vps_ctl.sh) makes
  # fetch-dg halt the batch (recording stopped:true in stats). Without this
  # break the loop would march straight into the next batch — a halt must stay
  # halted. Resume with:
  #   sudo systemctl restart tgpc-dg-fetch   (VM) — or vps_ctl.sh resume.
  # NOTE: no --warp-rotate-every here. Consumer WARP egress is sticky per
  # account, so the rotation gate can never verify a *different* IP and halts
  # every 500 records instead. The tunnel itself (masked egress) is what
  # matters and is established above; blocks are monitored via fail reasons.
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('data/dg_stats.json')).get('stopped') else 1)" 2>/dev/null; then
    reason=$(python3 -c "import json; print(json.load(open('data/dg_stats.json')).get('stop_reason','stop file'))" 2>/dev/null)
    echo "halted ($reason) — loop stopped (restart service to resume)"
    break
  fi
  # fetch-dg exit 0 covers batch-complete; loop on.
done
backup_checkpoint
echo "vps_fetch.sh finished"
