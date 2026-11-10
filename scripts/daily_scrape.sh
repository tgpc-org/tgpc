#!/usr/bin/env bash
# Scheduled scrape entry point — the launchd job `com.tgpc.scrape` calls this.
#
# Why this exists: the council only answers Indian IPs, and this Mac is the
# only host that has one. launchd (not cron) because it runs a missed job when
# the machine wakes, and because a user agent runs in the login session where
# the Keychain credentials live. See DESIGN.md -> "When the source blocks this
# host" for the egress-switch runbook.
#
# Each invocation is cheap by design:
#   - one run at a time (atomic mkdir lock; macOS has no flock)
#   - skip the slot entirely when today's run already succeeded, so the
#     10:00/14:00/18:00 slots only cost anything on a day that failed
#   - keep the Mac awake for the duration (a sleeping Mac kills the run)
#   - leave the outcome in data/last_run.json, written by `tgpc scrape`
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p data/logs
LOG="data/logs/scheduled-scrape.log"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# Atomic lock: mkdir succeeds for exactly one caller. A stale lock (crash,
# power loss) is cleared by the next slot rather than blocking the day forever.
LOCK="data/.scrape.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null | grep -q .; then
    rmdir "$LOCK" 2>/dev/null
    echo "$(date '+%F %T') cleared a stale lock" >> "$LOG"
    mkdir "$LOCK" 2>/dev/null || { echo "$(date '+%F %T') lock busy — skipping" >> "$LOG"; exit 0; }
  else
    echo "$(date '+%F %T') another scrape is running — skipping" >> "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Already done today? Nothing to do. This is what makes the retry slots free.
if "$PY" -m tgpc.run_status --check-today >> "$LOG" 2>&1; then
  echo "$(date '+%F %T') already succeeded today — skipping" >> "$LOG"
  exit 0
fi

echo "$(date '+%F %T') === scheduled scrape ===" >> "$LOG"
caffeinate -i make scrape >> "$LOG" 2>&1
rc=$?
echo "$(date '+%F %T') exit=$rc" >> "$LOG"
exit "$rc"
