#!/usr/bin/env bash
# Bootstrap a cloud Ubuntu VM (GCE e2-micro / Oracle A1 / Hetzner) for TGPC DG fetching.
# Run ONCE on the fresh VM as the normal user (uses sudo for apt only).
#
# Installs: python3-venv, git, curl, tesseract-ocr (captcha OCR),
#           rclone (GDrive slice).
# NOTE: no Cloudflare WARP by design (2026-09-30 decision). GCE IPs may not
# be blocked the way Actions IPs were — the smoke test proves it. If fetches
# come back BlockedError-streaked, re-add WARP: apt-install cloudflare-warp
# from https://pkg.cloudflareclient.com, `warp-cli --accept-tos registration
# new`, and connect per run (never autostart on a headless box — it
# blackholes inbound SSH). See git history of this file for the old block.
# Clones the repo, installs it, pulls data/rph.json from Supabase Storage
# (public object), and creates ~/.tgpc_env with secret placeholders.
set -euo pipefail

REPO_URL="${TGPC_REPO_URL:-https://github.com/tgpc-org/tgpc.git}"
TARGET_DIR="${TGPC_DIR:-$HOME/tgpc}"

echo "==> apt packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv git curl tesseract-ocr rclone lsb-release gnupg

echo "==> repo"
if [ ! -d "$TARGET_DIR/.git" ]; then
  git clone "$REPO_URL" "$TARGET_DIR"
fi
cd "$TARGET_DIR"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e .
python3 -c "import pytesseract, boto3, PIL; print('py-deps ok')"
tesseract --version | head -1

echo "==> secrets template (~/.tgpc_env)"
if [ ! -f "$HOME/.tgpc_env" ]; then
  cat > "$HOME/.tgpc_env" <<'EOF'
# Source this before running the fetch (or use the systemd unit, which loads it).
# Fill in real values — NEVER commit this file.
export SUPABASE_URL=
export SUPABASE_SECRET_KEY=
export R2_ACCESS_KEY_ID=
export R2_SECRET_ACCESS_KEY=
export CLOUDFLARE_ACCOUNT_ID=
export TGPC_R2_DG_BUCKET=tgpc-dg-private
# Optional (GDrive slice is skipped gracefully when absent):
export RCLONE_GDRIVE_CONFIG=
EOF
  chmod 600 "$HOME/.tgpc_env"
  echo "created ~/.tgpc_env — fill it in before running anything"
else
  echo "$HOME/.tgpc_env already exists — leaving it alone"
fi

echo "==> data/rph.json (reference for serial ordering)"
if [ ! -f "$TARGET_DIR/data/rph.json" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.tgpc_env" 2>/dev/null || true
  if [ -n "${SUPABASE_URL:-}" ]; then
    mkdir -p "$TARGET_DIR/data"
    curl -fsSL "$SUPABASE_URL/storage/v1/object/tgpc/rph.json" -o "$TARGET_DIR/data/rph.json"
    rph_path="$TARGET_DIR/data/rph.json"
    python3 -c "import json; print('rph.json ok:', len(json.load(open('$rph_path'))), 'records')"
  else
    echo "SUPABASE_URL not set yet — download data/rph.json later with:"
    echo "  curl -fsSL \"\$SUPABASE_URL/storage/v1/object/tgpc/rph.json\" -o data/rph.json"
  fi
else
  echo "data/rph.json already present"
fi

echo
echo "DONE. Next:"
echo "  1. Fill in ~/.tgpc_env (chmod 600, already set)"
echo "  2. From your Mac: scp data/dg_fetch_checkpoint.json ubuntu@<vm>:~/tgpc/data/  (keeps 8k+ terminal skips)"
echo "  3. Smoke test:  ./scripts/vps_fetch.sh --smoke   (50 records, proves direct fetch works)"
echo "  4. Long run:    sudo cp scripts/tgpc-dg-fetch.service /etc/systemd/system/ && sudo systemctl enable --now tgpc-dg-fetch"
