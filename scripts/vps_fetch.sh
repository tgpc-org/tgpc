#!/usr/bin/env bash
# DG fetch harness for the VPS. One continuous run over everything remaining,
# then it stops by itself — no batches, no endless loop.
#
# Safety without chunking: fetch-dg checkpoints after EVERY record and checks
# data/dg_stop before every record, so a supervisor below watches the run and
# stops it after the current record on halt, re-verifies the WARP tunnel, and
# backs the checkpoint up to R2 along the way.
#
# Usage:
#   ./scripts/vps_fetch.sh [--smoke]
#     --smoke   50-record trial (proves WARP egress + creds before a long run)
#
# Stop: touch data/dg_halt, or scripts/vps_ctl.sh stop (R2 halt flag) —
#       the run finishes the current record, then exits (checkpoint-safe).
# Remote (no SSH): scripts/vps_ctl.sh halt|resume|status (R2 ops/ctl.json channel)
set -euo pipefail

SMOKE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --smoke) SMOKE=1; shift ;;
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
  python3 - "$TGPC_R2_DG_BUCKET" <<'PYEOF' || echo "WARNING: R2 checkpoint backup failed — local checkpoint intact, will retry at the next backup" >&2
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
  # TGPC_DG_RETRY_TERMINAL=1 also re-queues terminal (refused) records first
  # of all, for deliberate re-probes of the full non-saved set.
  python3 - "$1" <<'PYEOF'
import json, os, sys
count = int(sys.argv[1])
retry_terminal = os.environ.get("TGPC_DG_RETRY_TERMINAL", "0") == "1"
rows = json.load(open("data/rph.json"))
order = {r["registration_number"]: (r.get("serial_number") is None, r.get("serial_number") or 0)
         for r in rows if r.get("registration_number")}
try:
    cp = json.load(open("data/dg_fetch_checkpoint.json"))
except FileNotFoundError:
    cp = {}
completed = set(cp.get("completed", []))
terminal = set(cp.get("failed_terminal", {}))
done = set(completed) if retry_terminal else (completed | terminal)
retryable = sorted([r for r in cp.get("failed", {}) if r not in done],
                   key=lambda r: order.get(r, (True, 0)))
seen = set(retryable)
if retry_terminal:
    term = sorted([r for r in terminal if r not in completed],
                  key=lambda r: order.get(r, (True, 0)))
    retryable = term + retryable
    seen |= set(term)
fresh = sorted([reg for reg in order if reg not in done and reg not in seen],
               key=lambda r: order.get(r, (True, 0)))
# count <= 0 means everything remaining (no chunking — one continuous run).
ids = (retryable + fresh) if count <= 0 else (retryable + fresh)[:count]
open("data/dg_ids_vps.txt", "w").write("\n".join(ids) + ("\n" if ids else ""))
print(f"ids: {len(ids)} ({len([i for i in ids if i in set(retryable)])} retryable)"
      + (" [retry-terminal]" if retry_terminal else ""))
PYEOF
}

FETCH_EXTRA_ARGS=""
if [ "${TGPC_DG_RETRY_TERMINAL:-0}" = "1" ]; then
  FETCH_EXTRA_ARGS="--retry-terminal"
fi

# --- main loop ----------------------------------------------------------------
if [ "$SMOKE" = "1" ]; then
  echo "=== SMOKE: 50 records via WARP tunnel ==="
  gen_ids 50
  [ -s data/dg_ids_vps.txt ] || { echo "nothing to fetch"; exit 0; }
  before=$(python3 -c "import json;s=json.load(open('data/dg_stats.json'));print(str(s.get('done',0))+' '+str(s.get('failed',0)))" 2>/dev/null || echo "0 0")
  python3 -m tgpc fetch-dg --ids-file data/dg_ids_vps.txt \
    --sync-cloud --sync-every 50 $FETCH_EXTRA_ARGS
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

completed_count() {
  python3 -c "import json; print(len(json.load(open('data/dg_fetch_checkpoint.json')).get('completed', [])))" 2>/dev/null || echo 0
}

# One continuous run over everything remaining, then stop. A supervisor
# watches the background fetch: R2 halt flag or data/dg_halt stops it after
# the current record (dg_stop), the WARP tunnel is re-verified, and the
# checkpoint is backed up to R2 every ~10 minutes. When the pool empties the
# run ends by itself; a run that saves nothing proves the source gaps are
# unchanged — rerun next week for the next attempt.
RUN_BEFORE=$(completed_count)
echo "=== RUN (completed so far: $RUN_BEFORE) ==="
gen_ids 0
[ -s data/dg_ids_vps.txt ] || { echo "nothing remaining — exiting"; backup_checkpoint; echo "vps_fetch.sh finished"; exit 0; }
rm -f data/dg_stop
echo "records this run: $(wc -l < data/dg_ids_vps.txt)"

python3 -m tgpc fetch-dg --ids-file data/dg_ids_vps.txt \
  --sync-cloud --sync-every 50 $FETCH_EXTRA_ARGS &
FETCH_PID=$!
POLLS=0
while kill -0 "$FETCH_PID" 2>/dev/null; do
  sleep 60
  POLLS=$((POLLS + 1))
  # Halt (file or R2 flag) stops the run after the current record.
  if [ -f data/dg_halt ] || ! poll_ctl; then
    echo "halt requested — stopping after the current record…"
    touch data/dg_stop
  fi
  # A dropped tunnel would silently go direct (datacenter IP = blocks).
  if ! warp-cli --accept-tos status 2>/dev/null | grep -qi "connected"; then
    echo "tunnel down mid-run — reconnecting…"
    warp_up
  fi
  if [ $((POLLS % 10)) -eq 0 ]; then
    backup_checkpoint
  fi
done
RC=0
wait "$FETCH_PID" || RC=$?
echo "fetch exited ($RC)."
backup_checkpoint
RUN_AFTER=$(completed_count)
RUN_SAVED=$((RUN_AFTER - RUN_BEFORE))
echo "=== RUN done: +$RUN_SAVED saved ($RUN_BEFORE -> $RUN_AFTER) ==="
if python3 -c "import json,sys; sys.exit(0 if json.load(open('data/dg_stats.json')).get('stopped') else 1)" 2>/dev/null; then
  reason=$(python3 -c "import json; print(json.load(open('data/dg_stats.json')).get('stop_reason','stop file'))" 2>/dev/null)
  echo "halted ($reason) — staying stopped (start again to resume from the checkpoint)"
elif [ "${TGPC_DG_RETRY_TERMINAL:-0}" = "1" ] && [ "$RUN_SAVED" -eq 0 ]; then
  echo "run saved nothing — source gaps unchanged, parking until next week"
else
  echo "run complete — staying stopped (start again whenever wanted)"
fi
echo "vps_fetch.sh finished"
