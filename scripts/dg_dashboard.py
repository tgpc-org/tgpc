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
    """Merge live + stats + checkpoint + all-time history into one payload.

    Per-run files (dg_stats.json) reset every run; dg_history.jsonl is the
    permanent memory — one line per processed record, never rewritten.
    """
    live = load_json(data_dir / "dg_live.json")
    stats = load_json(data_dir / "dg_stats.json")
    checkpoint = load_json(data_dir / "dg_fetch_checkpoint.json")
    stop_armed = (data_dir / "dg_stop").exists()
    total = live.get("total") or stats.get("total") or 0
    processed = stats.get("done", 0) + stats.get("failed", 0) + stats.get("quarantined", 0)
    all_time = {"saved": 0, "failed": 0, "quarantined": 0}
    history_path = data_dir / "dg_history.jsonl"
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    outcome = json.loads(line).get("outcome")
                except Exception:
                    continue
                if outcome in all_time:
                    all_time[outcome] += 1
    except FileNotFoundError:
        pass
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
        "quarantined": stats.get("quarantined", 0),
        "all_time": all_time,
        "history_records": sum(all_time.values()),
        "sb_upserted": stats.get("sb_upserted", 0),
        "captcha_firstpass_ok": stats.get("captcha_firstpass_ok", 0),
        "captcha_retries": stats.get("captcha_retries", 0),
        "fail_by_reason": stats.get("fail_by_reason", {}),
        "records_per_min": live.get("records_per_min"),
        "eta_mins": live.get("eta_mins"),
        "stop_armed": stop_armed,
        "completed": len(checkpoint.get("completed", [])),
        "terminal": len(checkpoint.get("failed_terminal", {})),
        "updated_at": live.get("updated_at", ""),
        "updated_ist": to_ist_day(live.get("updated_at", "")),
        "next_reg": live.get("next_reg", ""),
        "next_serial": live.get("next_serial"),
        "remaining_in_batch": live.get("remaining_in_batch"),
    }


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
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/stop":
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
                json.dumps({"ok": True, "pid": proc.pid, "count": len(ids), "first": ids[0], "last": ids[-1]}).encode(),
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
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"DG dashboard: http://127.0.0.1:{args.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
