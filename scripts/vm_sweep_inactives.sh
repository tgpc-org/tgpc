#!/usr/bin/env bash
# Re-check Retired/Inactive records against the live source on the VM.
# Reads ~/tgpc/data/inactive_records.jsonl (one JSON object per line),
# writes now-Active hits to ~/tgpc/data/now_active_from_inactive.jsonl.
set -euo pipefail
cd "$HOME/tgpc"

# shellcheck disable=SC1091
. "$HOME/.tgpc_env"
export SUPABASE_URL SUPABASE_SECRET_KEY R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY CLOUDFLARE_ACCOUNT_ID

WORKERS="${WORKERS:-8}"
MIN_DELAY="${MIN_DELAY:-0.5}"

echo "starting sweep: $(wc -l < data/inactive_records.jsonl) records, ${WORKERS} workers"
exec .venv/bin/python -m tgpc.inactive_sweep sweep --workers "$WORKERS" --min-delay "$MIN_DELAY"
