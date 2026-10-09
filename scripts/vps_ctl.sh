#!/usr/bin/env bash
# Remote control for the VPS fetch loop via R2 — no SSH needed.
# The loop polls ops/ctl.json in the private DG bucket before every batch:
#   {"halt": false, "ssh_exclude": "<cidrs>", "updated_at": "...", "note": "..."}
# Commands:
#   ./scripts/vps_ctl.sh status            # progress from R2 backups (no SSH)
#   ./scripts/vps_ctl.sh halt [note]       # pause loop after current batch
#   ./scripts/vps_ctl.sh resume            # clear halt (loop resumes on next poll)
#   ./scripts/vps_ctl.sh exclude CIDR...   # set WARP SSH exclusions (applied next batch)
#   ./scripts/vps_ctl.sh show              # print current ctl.json
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -z "${TGPC_R2_DG_BUCKET:-}" ]; then
  # Fall back to the macOS Keychain (same convention as the python loader).
  TGPC_R2_DG_BUCKET=$(python3 -c "from tgpc.utils import _get_keychain; print(_get_keychain('TGPC_R2_DG_BUCKET') or '')" 2>/dev/null)
fi
: "${TGPC_R2_DG_BUCKET:?set TGPC_R2_DG_BUCKET (or source ~/.tgpc_env / use keychain env)}"

_py() {
  R2_BUCKET="$TGPC_R2_DG_BUCKET" python3 - "$@"
}

case "${1:-status}" in
  status)
    _py <<'PYEOF'
import os, json
from tgpc.utils import _get_keychain
import boto3
b = os.environ["R2_BUCKET"]
e = {n: os.environ.get(n) or _get_keychain(n) for n in
     ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID")}
s3 = boto3.client("s3", endpoint_url=f"https://{e['CLOUDFLARE_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                  aws_access_key_id=e["R2_ACCESS_KEY_ID"], aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"])
s3.download_file(b, "ops/dg_stats.json", "/tmp/r2_stats.json")
s3.download_file(b, "ops/dg_fetch_checkpoint.json", "/tmp/r2_cp.json")
s = json.load(open("/tmp/r2_stats.json"))
cp = json.load(open("/tmp/r2_cp.json"))
print("stats updated: ", s.get("updated_at"))
print("batch:        ", s.get("done"), "done /", s.get("failed"), "failed")
print("reasons:      ", s.get("fail_by_reason"))
print("stopped:      ", s.get("stopped"), s.get("stop_reason"))
print("checkpoint completed:", len(cp.get("completed", [])))
PYEOF
    ;;
  show)
    _py <<'PYEOF'
import os, json
from tgpc.utils import _get_keychain
import boto3
b = os.environ["R2_BUCKET"]
e = {n: os.environ.get(n) or _get_keychain(n) for n in
     ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID")}
s3 = boto3.client("s3", endpoint_url=f"https://{e['CLOUDFLARE_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                  aws_access_key_id=e["R2_ACCESS_KEY_ID"], aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"])
try:
    s3.download_file(b, "ops/ctl.json", "/tmp/r2_ctl.json")
    print(open("/tmp/r2_ctl.json").read())
except Exception as ex:
    print(f"(no ctl.json yet: {ex})")
PYEOF
    ;;
  halt|resume|exclude)
    cmd="$1"; shift
    _py "$cmd" "$*" <<'PYEOF'
import os, sys, json, datetime
from tgpc.utils import _get_keychain
import boto3
b = os.environ["R2_BUCKET"]
cmd = sys.argv[1]
rest = sys.argv[2] if len(sys.argv) > 2 else ""
e = {n: os.environ.get(n) or _get_keychain(n) for n in
     ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID")}
s3 = boto3.client("s3", endpoint_url=f"https://{e['CLOUDFLARE_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                  aws_access_key_id=e["R2_ACCESS_KEY_ID"], aws_secret_access_key=e["R2_SECRET_ACCESS_KEY"])
try:
    s3.download_file(b, "ops/ctl.json", "/tmp/r2_ctl.json")
    ctl = json.load(open("/tmp/r2_ctl.json"))
except Exception:
    ctl = {"halt": False, "ssh_exclude": "", "updated_at": "", "note": ""}
if cmd == "halt":
    ctl["halt"] = True
    ctl["note"] = rest or "manual halt"
elif cmd == "resume":
    ctl["halt"] = False
    ctl["note"] = rest or "resumed"
elif cmd == "exclude":
    ctl["ssh_exclude"] = rest
    ctl["note"] = f"exclusions: {rest}"
ctl["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
open("/tmp/r2_ctl.json", "w").write(json.dumps(ctl, indent=2))
s3.upload_file("/tmp/r2_ctl.json", b, "ops/ctl.json")
print("ctl.json updated:", json.dumps(ctl))
PYEOF
    ;;
  *)
    echo "usage: $0 {status|show|halt [note]|resume|exclude CIDR...}" >&2
    exit 2
    ;;
esac
