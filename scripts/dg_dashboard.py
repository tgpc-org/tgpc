"""Local watch-only dashboard for DG extraction runs (stdlib only, localhost).

Usage:
    python3 scripts/dg_dashboard.py [--port 8765]
Then open http://127.0.0.1:8765 in a browser. Single page, auto-refreshes.
Read-only: it never launches or stops anything — control lives in the
terminal (./scripts/dg_run.sh, ./scripts/vps_ctl.sh, touch data/dg_stop)
and in the admin OPS tab. Binds localhost only — never exposed to a network.
"""

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGE = Path(__file__).resolve().parent / "dg_dashboard.html"

# VPS remote-fetch visibility: R2 control channel (read-only here).
VM_FILE = DATA / "dg_vm.conf"  # plain "user@host", gitignored like all of data/
R2_OPS_FRESH_MIN = 15  # R2 ops backup fresher than this counts as a live loop

IST = timezone(timedelta(hours=5, minutes=30))


def to_ist_day(utc_iso: str) -> str:
    """UTC ISO -> 'Thu-18-09-2026 17:45 IST'. Empty in, empty out."""
    if not utc_iso:
        return ""
    try:
        dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone(IST)
    except Exception:
        return utc_iso
    return dt.strftime("%a-%d-%m-%Y %H:%M IST")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def tail_lines(path: Path, n: int = 30) -> list:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return lines[-n:]


def next_ids(data_dir: Path, count: int = 500, rph_path: Optional[Path] = None) -> list:
    """Next `count` reg IDs in serial order: retryable failures first, then fresh.

    Retryable = in checkpoint `failed` but not terminal (a later run is their
    only path back — terminal IDs are skipped forever, completed are done).
    Ordering contract mirrored by gen_ids() in scripts/vps_fetch.sh.
    """
    try:
        rows = json.loads((rph_path or ROOT / "data" / "rph.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    order = {r["registration_number"]: (r.get("serial_number") is None, r.get("serial_number") or 0) for r in rows}
    try:
        checkpoint = json.loads((data_dir / "dg_fetch_checkpoint.json").read_text(encoding="utf-8"))
    except Exception:
        checkpoint = {}
    done = set(checkpoint.get("completed", [])) | set(checkpoint.get("failed_terminal", {}))
    retryable = [r for r in checkpoint.get("failed", {}) if r not in done]
    fresh = [reg for reg in order if reg not in done and reg not in set(retryable)]
    retryable.sort(key=lambda r: order.get(r, (True, 0)))
    fresh.sort(key=lambda r: order.get(r, (True, 0)))
    return (retryable + fresh)[: max(0, count)]


def build_status(data_dir: Path = DATA) -> dict:
    """Merge live + stats + checkpoint into one payload.

    Per-run files (dg_stats.json) reset every run. All-time numbers come
    from the checkpoint (unique regs) — never from dg_history.jsonl line
    counts, which include duplicate re-save lines. The history file remains
    as an audit log only.
    """
    live = load_json(data_dir / "dg_live.json")
    stats = load_json(data_dir / "dg_stats.json")
    checkpoint = load_json(data_dir / "dg_fetch_checkpoint.json")
    completed = checkpoint.get("completed", [])
    terminal = checkpoint.get("failed_terminal", {})
    stop_armed = (data_dir / "dg_stop").exists()
    total = live.get("total") or stats.get("total") or 0
    processed = stats.get("done", 0) + stats.get("failed", 0)
    all_time = {"completed": len(completed), "refused": len(terminal)}
    return {
        "status": live.get("status", "idle"),
        "current_reg": live.get("current_reg", ""),
        "serial_number": live.get("serial_number"),
        "event": live.get("event", ""),
        "total": total,
        "processed": processed,
        "done": stats.get("done", 0),
        "failed": stats.get("failed", 0),
        "all_time": all_time,
        "resolved": sum(all_time.values()),
        "sb_upserted": stats.get("sb_upserted", 0),
        "captcha_firstpass_ok": stats.get("captcha_firstpass_ok", 0),
        "captcha_retries": stats.get("captcha_retries", 0),
        "fail_by_reason": stats.get("fail_by_reason", {}),
        "records_per_min": live.get("records_per_min"),
        "eta_mins": live.get("eta_mins"),
        "stop_armed": stop_armed,
        "completed": len(completed),
        "updated_at": live.get("updated_at", ""),
        "updated_ist": to_ist_day(live.get("updated_at", "")),
        "next_reg": live.get("next_reg", ""),
        "next_serial": live.get("next_serial"),
        "remaining_in_batch": live.get("remaining_in_batch"),
    }


def _load_env_file() -> None:
    """Source KEY=VALUE lines from ~/.tgpc_env (never overwrites real env).

    Deterministic credential source for background/launchd contexts where
    macOS Keychain prompts are unreliable. Same file the VPS runbook uses.
    """
    try:
        path = Path(os.path.expanduser("~/.tgpc_env"))
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and value and not os.environ.get(key):
                os.environ[key] = value
    except Exception:
        pass


def read_vm() -> str:
    """VM address: DG_VM env wins, else the persisted data/dg_vm.conf."""
    env_vm = (os.environ.get("DG_VM") or "").strip()
    if env_vm:
        return env_vm
    try:
        return VM_FILE.read_text(encoding="utf-8").strip().split()[0]
    except Exception:
        return ""


def _cred(name: str) -> str:
    """Env first, macOS Keychain fallback (same convention as vps_ctl.sh)."""
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        from tgpc.utils import _get_keychain  # noqa: PLC0415

        return _get_keychain(name) or ""
    except Exception:
        return ""


def _r2():
    """Boto3 client against the private DG bucket (lazy import: stdlib-only at import time)."""
    import boto3  # noqa: PLC0415

    bucket = os.environ.get("TGPC_R2_DG_BUCKET", "tgpc-dg-private")
    account = _cred("CLOUDFLARE_ACCOUNT_ID")
    access = _cred("R2_ACCESS_KEY_ID")
    secret = _cred("R2_SECRET_ACCESS_KEY")
    if not all([account, access, secret]):
        raise RuntimeError("R2 credentials missing (env or Keychain)")
    return (
        boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            region_name="auto",
            aws_access_key_id=access,
            aws_secret_access_key=secret,
        ),
        bucket,
    )


def vps_state() -> dict:
    """Read the loop's R2 ops snapshot. Never raises — errors become {"error": ...}."""
    try:
        s3, bucket = _r2()
        stats_raw = s3.get_object(Bucket=bucket, Key="ops/dg_stats.json")["Body"].read()
        ctl = s3.get_object(Bucket=bucket, Key="ops/ctl.json")["Body"].read()
        stats, ctl_doc = json.loads(stats_raw), json.loads(ctl)
        updated = stats.get("updated_at", "")
        try:
            age_min = (
                datetime.now(timezone.utc) - datetime.fromisoformat(updated.replace("Z", "+00:00"))
            ).total_seconds() / 60
        except Exception:
            age_min = None
        return {
            "done": stats.get("done", 0),
            "failed": stats.get("failed", 0),
            "fail_by_reason": stats.get("fail_by_reason", {}),
            "updated_at": updated,
            "age_min": round(age_min, 1) if age_min is not None else None,
            "alive": age_min is not None and age_min < R2_OPS_FRESH_MIN and not ctl_doc.get("halt", False),
            "halt": bool(ctl_doc.get("halt", False)),
            "note": ctl_doc.get("note", ""),
        }
    except Exception as e:
        return {"error": str(e)[:160]}


def drift_state() -> dict:
    """Local checkpoint completed vs Supabase DG rows. Never raises."""
    try:
        local = len(load_json(DATA / "dg_fetch_checkpoint.json").get("completed", []))
    except Exception:
        return {"error": "local checkpoint unreadable"}
    try:
        from supabase import create_client  # noqa: PLC0415

        url = _cred("SUPABASE_URL")
        key = _cred("SUPABASE_SECRET_KEY")
        if not url or not key:
            return {"local_completed": local, "error": "Supabase credentials missing"}
        sb = create_client(url, key)
        n, off = 0, 0
        while True:
            page = (
                sb.table("rph_dg_contacts")
                .select("registration_number")
                .order("registration_number")
                .range(off, off + 1999)
                .execute()
                .data
                or []
            )
            n += len(page)
            if len(page) < 2000:
                break
            off += 2000
        return {"local_completed": local, "db_rows": n, "drift": n - local}
    except Exception as e:
        return {"local_completed": local, "error": str(e)[:160]}


_OVERVIEW_CACHE: dict = {"at": 0.0, "data": None}
OVERVIEW_TTL = 60.0


def _sb_counts() -> dict:
    """Fast exact counts via PostgREST Prefer: count=exact (no row transfer)."""
    import urllib.request  # noqa: PLC0415

    url = _cred("SUPABASE_URL")
    key = _cred("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError("Supabase credentials missing")
    out = {}
    for table in ("rph", "rph_dg_contacts"):
        req = urllib.request.Request(
            f"{url}/rest/v1/{table}?select=registration_number",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Prefer": "count=exact",
                "Range": "0-0",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            total = resp.headers.get("Content-Range", "").split("/")[-1]
            out[table] = int(total) if total.isdigit() else -1
    req = urllib.request.Request(
        f"{url}/rest/v1/metadata?key=eq.last_sync&select=value",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.loads(resp.read() or b"[]")
        out["last_sync"] = rows[0]["value"] if rows else ""
    return out


def _gh_runs() -> list:
    """Latest CI runs via gh (best-effort, short timeout)."""
    try:
        proc = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--repo",
                "tgpc-org/tgpc",
                "--limit",
                "5",
                "--json",
                "name,conclusion,status,headBranch,createdAt",
                "--jq",
                '.[] | "\\(.createdAt[0:16]) \\(.name) \\(.headBranch) \\(.conclusion // .status)"',
            ],
            capture_output=True,
            text=True,
            timeout=25,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            return []
        return [ln for ln in proc.stdout.splitlines() if ln.strip()][:5]
    except Exception:
        return []


def build_overview() -> dict:
    """Whole-operation snapshot. Cached 60s. Every source best-effort with
    per-key errors — one dead source never blanks the page."""
    import time as _time

    now = _time.time()
    if _OVERVIEW_CACHE["data"] is not None and now - _OVERVIEW_CACHE["at"] < OVERVIEW_TTL:
        return _OVERVIEW_CACHE["data"]
    out: dict = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    try:
        rph_path = DATA / "rph.json"
        rows = json.loads(rph_path.read_text(encoding="utf-8"))
        regs = {r["registration_number"] for r in rows}
        out["local"] = {
            "rph_rows": len(rows),
            "rph_mtime": datetime.fromtimestamp(rph_path.stat().st_mtime, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
    except Exception as e:
        out["local"] = {"error": str(e)[:120]}
        regs = set()

    try:
        out["cloud"] = _sb_counts()
    except Exception as e:
        out["cloud"] = {"error": str(e)[:120]}

    try:
        cp = load_json(DATA / "dg_fetch_checkpoint.json")
        completed = set(cp.get("completed", []))
        terminal = set(cp.get("failed_terminal", {}))
        uncovered = sorted(regs - completed - terminal) if regs else []
        out["dg"] = {
            "completed": len(completed),
            "terminal": len(terminal),
            "uncovered": len(uncovered),
            "uncovered_sample": uncovered[:6],
        }
    except Exception as e:
        out["dg"] = {"error": str(e)[:120]}

    out["vps"] = vps_state()
    out["ci"] = _gh_runs()
    _OVERVIEW_CACHE["at"] = now
    _OVERVIEW_CACHE["data"] = out
    return out


class Handler(BaseHTTPRequestHandler):
    data_dir: Path = DATA

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._send(json.dumps(build_status(self.data_dir)).encode(), "application/json")
        elif self.path.startswith("/api/log"):
            self._send(json.dumps(tail_lines(self.data_dir / "dg_fetch.log")).encode(), "application/json")
        elif self.path == "/api/vps/status":
            vps = vps_state()
            drift = drift_state()
            self._send(
                json.dumps(
                    {
                        "vm": read_vm(),
                        "vm_configured": bool(read_vm()),
                        "vps": vps,
                        "drift": drift,
                    }
                ).encode(),
                "application/json",
            )
        elif self.path == "/api/overview":
            self._send(json.dumps(build_overview()).encode(), "application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: object) -> None:
        pass  # keep console clean; dashboard state lives in data files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    # Resolve credentials once here (foreground terminal: Keychain works)
    # and keep them in-process env for request threads — Keychain prompts
    # are unreliable from background worker threads.
    _load_env_file()
    for name in (
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "CLOUDFLARE_ACCOUNT_ID",
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
    ):
        try:
            if not os.environ.get(name) and _cred(name):
                os.environ[name] = _cred(name)
        except Exception:
            pass
    missing = [n for n in ("R2_ACCESS_KEY_ID", "SUPABASE_SECRET_KEY") if not os.environ.get(n)]
    if missing:
        print(f"warning: missing {', '.join(missing)} — VPS endpoints will error.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"DG dashboard: http://127.0.0.1:{args.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
