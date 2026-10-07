"""Tiny local dashboard for DG extraction runs (stdlib only, localhost).

Usage:
    python3 scripts/dg_dashboard.py [--port 8765]
Then open http://127.0.0.1:8765 in a browser. Single page, auto-refreshes
every 2s. The STOP button touches data/dg_stop (run halts after the current
record, checkpoint-safe). Binds localhost only — never exposed to a network.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGE = Path(__file__).resolve().parent / "dg_dashboard.html"
PIDFILE = DATA / "dg_fetch.pid"
RUNLOG = DATA / "dg_fetch_run.log"

# VPS remote-fetch control: R2 control channel + dg_run.sh launcher.
VPS_LAUNCH_LOG = DATA / "dg_run_launch.log"
VPS_LAUNCH_PID = DATA / "dg_run.pid"
ACTION_LOG = DATA / "dg_dashboard_actions.log"
VM_FILE = DATA / "dg_vm.conf"  # plain "user@host", gitignored like all of data/
R2_OPS_FRESH_MIN = 15  # R2 ops backup fresher than this counts as a live loop
DG_RUN_SCRIPT = ROOT / "scripts" / "dg_run.sh"

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


def run_active(data_dir: Path = DATA) -> bool:
    """True if a fetch run is currently alive (pidfile + process check).

    Reaps finished children (zombies): a dead pid — even as zombie — means
    not active, and the stale pidfile is removed so a later run can start.
    """
    try:
        pid = int((data_dir / "dg_fetch.pid").read_text().strip())
    except Exception:
        return False
    try:
        finished, _ = os.waitpid(pid, os.WNOHANG)
        if finished == pid:
            try:
                (data_dir / "dg_fetch.pid").unlink()
            except OSError:
                pass
            return False
    except ChildProcessError:
        pass  # not our child — fall through to liveness probe
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        try:
            (data_dir / "dg_fetch.pid").unlink()  # fully gone, not just zombie
        except OSError:
            pass
        return False
    except Exception:
        return False


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
        "run_active": run_active(data_dir),
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


def audit(action: str, detail: str = "") -> None:
    """Append one line to the action log. Never raises (logging must not break control)."""
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(ACTION_LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {action} {detail}\n".rstrip() + "\n")
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


def launch_running(pidfile: Path = VPS_LAUNCH_PID) -> bool:
    """True if a dashboard-launched dg_run.sh is still alive."""
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        try:
            pidfile.unlink()
        except OSError:
            pass
        return False


def start_guards(
    vm: str,
    workers: object,
    local_completed: object,
    db_rows: object,
    vps_alive: bool,
    launch_is_running: bool,
) -> tuple:
    """Pure launch gate. Returns (ok, checks[]) — every refusal names its fix.

    Never touches the network or disk: callers gather the inputs (vps_state,
    drift_state, launch_running) so this stays unit-testable.
    """
    checks = []
    try:
        w = int(workers)  # type: ignore[arg-type]
    except Exception:
        w = 0
    if not vm or not isinstance(vm, str) or "@" not in vm:
        return False, ["VM not configured — set DG_VM=user@host (SSH key auth) and retry."]
    if not 1 <= w <= 16:
        return False, [f"workers must be 1..16 (got {workers})."]
    if not isinstance(local_completed, int):
        return False, ["local checkpoint unreadable — fix data/dg_fetch_checkpoint.json first."]
    if not isinstance(db_rows, int):
        checks.append("drift unverifiable (Supabase unreachable) — refusing: reconcile visibility first.")
        return False, checks
    if db_rows > local_completed:
        return False, [
            f"DB ({db_rows}) is ahead of local checkpoint ({local_completed}) — "
            "reconcile cloud->local first, or the VM will re-probe finished records."
        ]
    if launch_is_running:
        return False, ["a dashboard launch is already running — watch its log, do not double-start."]
    if vps_alive:
        return False, ["VPS loop looks alive (fresh R2 ops, not halted) — refusing a double start."]
    checks.append(f"preflight ok: vm={vm} workers={w} local={local_completed} db={db_rows}.")
    if w > 12:
        checks.append("note: starting above 12 risks a block_storm halt; 8 is the safe start.")
    return True, checks


def set_halt(halt: bool, note: str) -> dict:
    """Write ops/ctl.json halt flag. Returns the written doc (or {"error": ...})."""
    try:
        import datetime as _dt

        s3, bucket = _r2()
        try:
            ctl = json.loads(s3.get_object(Bucket=bucket, Key="ops/ctl.json")["Body"].read())
        except Exception:
            ctl = {}
        ctl["halt"] = halt
        ctl["note"] = note
        ctl["updated_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        s3.put_object(Bucket=bucket, Key="ops/ctl.json", Body=json.dumps(ctl, indent=2).encode())
        return ctl
    except Exception as e:
        return {"error": str(e)[:160]}


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
                        "launch_running": launch_running(),
                    }
                ).encode(),
                "application/json",
            )
        elif self.path == "/api/vps/launchlog":
            self._send(json.dumps(tail_lines(VPS_LAUNCH_LOG, 60)).encode(), "application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/vps/stop":
            vm = read_vm()
            ctl = set_halt(True, "dashboard STOP")
            ok = "error" not in ctl
            audit("vps-stop", f"vm={vm} ok={ok}")
            self._send(json.dumps({"ok": ok, "ctl": ctl}).encode(), "application/json")
        elif self.path == "/api/vps/start":
            body = self._read_json_body()
            try:
                workers = max(1, min(int(body.get("workers", 8)), 16))
            except Exception:
                workers = 8
            vm = read_vm()
            vps = vps_state()
            drift = drift_state()
            local_n = drift.get("local_completed")
            db_n = drift.get("db_rows")
            ok, checks = start_guards(
                vm,
                workers,
                local_n if isinstance(local_n, int) else None,
                db_n if isinstance(db_n, int) else None,
                bool(vps.get("alive", False)),
                launch_running(),
            )
            if not ok:
                audit("vps-start-refused", "; ".join(checks)[:200])
                self._send(json.dumps({"ok": False, "checks": checks}).encode(), "application/json")
                return
            env = dict(os.environ, DG_VM=vm)
            log = open(VPS_LAUNCH_LOG, "ab")
            proc = subprocess.Popen(
                [str(DG_RUN_SCRIPT), f"--workers={workers}"],
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
            VPS_LAUNCH_PID.write_text(str(proc.pid), encoding="utf-8")
            audit("vps-start", f"vm={vm} workers={workers} pid={proc.pid}")
            self._send(
                json.dumps({"ok": True, "pid": proc.pid, "checks": checks}).encode(),
                "application/json",
            )
        elif self.path == "/api/stop":
            (self.data_dir / "dg_stop").write_text("", encoding="utf-8")
            self._send(b'{"ok": true}', "application/json")
        elif self.path == "/api/start":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                body = {}
            try:
                count = max(1, min(int(body.get("count", 500)), 2000))
            except Exception:
                count = 500
            if run_active(self.data_dir):
                self._send(b'{"ok": false, "error": "run already active"}', "application/json")
                return
            ids = next_ids(self.data_dir, count)
            if not ids:
                self._send(b'{"ok": false, "error": "no IDs left"}', "application/json")
                return
            ids_file = self.data_dir / "dg_ids_dash.txt"
            ids_file.write_text("\n".join(ids) + "\n", encoding="utf-8")
            cmd = [
                sys.executable,
                "-m",
                "tgpc",
                "fetch-dg",
                "--ids-file",
                str(ids_file),
                "--captcha",
                "auto",
                "--sync-cloud",
                "--sync-every",
                "50",
            ]
            if body.get("warp_every"):
                try:
                    cmd += ["--warp-rotate-every", str(max(1, int(body["warp_every"])))]
                except Exception:
                    pass
            log = open(self.data_dir / "dg_fetch_run.log", "ab")
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
            (self.data_dir / "dg_fetch.pid").write_text(str(proc.pid), encoding="utf-8")
            self._send(
                json.dumps(
                    {
                        "ok": True,
                        "pid": proc.pid,
                        "count": len(ids),
                        "first": ids[0],
                        "last": ids[-1],
                    }
                ).encode(),
                "application/json",
            )
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
