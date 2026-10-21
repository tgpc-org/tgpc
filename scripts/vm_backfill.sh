#!/usr/bin/env bash
# Completeness backfill on the VM: fill blank rph columns, then re-fetch the
# handful of records still missing a photo.
set -euo pipefail
cd "$HOME/tgpc"

# shellcheck disable=SC1091
. "$HOME/.tgpc_env"

echo "=== [1/2] fill blank columns (gender/validity) ==="
.venv/bin/python -m tgpc.backfill_missing \
  --ids-file data/backfill_gender_rem.jsonl \
  --workers "${WORKERS:-8}" --min-delay "${MIN_DELAY:-0.5}"

echo
echo "=== [2/2] re-fetch records missing a photo ==="
cp data/backfill_photo_29.jsonl data/now_active_from_inactive.jsonl
.venv/bin/python -m tgpc.enrich_actives \
  --workers "${PHOTO_WORKERS:-4}" --min-delay "${MIN_DELAY:-0.5}"
