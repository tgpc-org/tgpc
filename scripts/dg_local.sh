#!/usr/bin/env bash
# DG contact fetch on this Mac.
#
# The council only answers Indian IPs, and this Mac is the host that has one,
# so the DG fetch runs here now (it used to run on a cloud VM). One bounded
# batch per run; the checkpoint is updated after every record, so an
# interrupted run resumes where it stopped.
#
# Cloud sync (Supabase + R2 + GDrive + Supabase Storage) happens as the run
# goes, so a finished batch is durable immediately — the local files are only
# the crash buffer.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p data/logs data/dg_raw
LOG="data/logs/dg-local.log"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# Records per night. The current backlog is ~600, which is roughly 20 minutes
# at the observed ~30 records/minute.
BATCH="${DG_BATCH:-600}"
IDS="data/dg_pending_ids.txt"

# The master list decides what is pending; without it there is nothing to do.
if [ ! -s data/rph.json ]; then
  echo "$(date '+%F %T') data/rph.json missing — run 'make scrape' first" >> "$LOG"
  exit 1
fi

"$PY" -m tgpc dg-pending --limit "$BATCH" --out "$IDS" >> "$LOG" 2>&1
if [ ! -s "$IDS" ]; then
  echo "$(date '+%F %T') nothing pending — DG is complete" >> "$LOG"
  exit 0
fi

echo "$(date '+%F %T') fetching $(wc -l < "$IDS" | tr -d ' ') records" >> "$LOG"
caffeinate -i "$PY" -m tgpc fetch-dg --ids-file "$IDS" \
  --sync-cloud --sync-every 50 --max-records "$BATCH" >> "$LOG" 2>&1
rc=$?
echo "$(date '+%F %T') exit=$rc" >> "$LOG"
exit "$rc"
