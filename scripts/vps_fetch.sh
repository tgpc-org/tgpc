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
warp_up() {
  warp-cli --accept-tos connect
  sleep 5
  # Excluded routes keep operator SSH reachable through an active tunnel.
  # 2026.x syntax: `tunnel ip add-range` (bare `add-excluded-route` is gone).
  for net in ${TGPC_SSH_EXCLUDE:-}; do
    warp-cli --accept-tos tunnel ip add-range "$net" 2>/dev/null \
      || warp-cli --accept-tos tunnel ip add "${net%%/*}" 2>/dev/null \
      || echo "WARNING: could not exclude $net from WARP — SSH may drop while tunnel is up" >&2
  done
  warp-cli status | head -2
  echo "egress now: $(curl -s --max-time 10 https://api.ipify.org)"
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
  gen_ids "$BATCH"
  [ -s data/dg_ids_vps.txt ] || { echo "ID pool exhausted — all done"; break; }
  python3 -m tgpc fetch-dg --ids-file data/dg_ids_vps.txt \
    --sync-cloud --sync-every 50 \
    --warp-rotate-every 500 --warp-max-cycles 3
  backup_checkpoint
  # fetch-dg exit 0 covers both batch-complete and STOP-file halt; loop on.
done
backup_checkpoint
echo "vps_fetch.sh finished"
