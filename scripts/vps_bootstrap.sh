#!/usr/bin/env bash
# Bootstrap a cloud Ubuntu VM (GCE e2-micro / Oracle A1 / Hetzner) for TGPC DG fetching.
# Run ONCE on the fresh VM as the normal user (uses sudo for apt only).
#
# Installs: python3-venv, git, curl, tesseract-ocr (captcha OCR),
#           rclone (GDrive slice), cloudflare-warp (egress masking —
#           the TGPC source blocks datacenter IPs, WARP is NOT optional).
# Clones the repo, installs it, pulls data/rph.json from Supabase Storage
# (public object), and creates ~/.tgpc_env with secret placeholders.
set -euo pipefail

REPO_URL="${TGPC_REPO_URL:-https://github.com/tgpc-org/tgpc.git}"
TARGET_DIR="${TGPC_DIR:-$HOME/tgpc}"

echo "==> apt packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv git curl tesseract-ocr rclone lsb-release gnupg

echo "==> cloudflare-warp"
if ! command -v warp-cli >/dev/null 2>&1; then
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
    | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq cloudflare-warp
fi
# Headless-box rule: the tunnel must NEVER autostart. An unattended connect
# reroutes the default gateway and blackholes inbound SSH (we got locked out
# exactly this way). The daemon runs; the tunnel stays down until vps_fetch.sh
# connects explicitly around a run.
sudo systemctl enable warp-svc 2>/dev/null || true
sudo systemctl start warp-svc 2>/dev/null || sudo service warp-svc start 2>/dev/null || true
sleep 5
# New (2026.x) CLI: `registration new` (`register` is gone).
warp-cli --accept-tos registration new 2>/dev/null || echo "(warp already registered)"
warp-cli --accept-tos disconnect 2>/dev/null || true
echo "(WARP installed + registered, tunnel DOWN by design — fetch script connects per run)"

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
# Optional: space-separated IPs/CIDRs that must bypass WARP (operator SSH).
# The fetch script adds these as excluded routes right after connecting, so an
# active tunnel never blackholes your SSH session. Example: "49.37.155.244/32"
export TGPC_SSH_EXCLUDE=
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
echo "  3. Smoke test:  ./scripts/vps_fetch.sh --smoke   (50 records, proves WARP egress works)"
echo "  4. Long run:    sudo cp scripts/tgpc-dg-fetch.service /etc/systemd/system/ && sudo systemctl enable --now tgpc-dg-fetch"
