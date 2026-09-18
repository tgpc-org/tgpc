"""Tiny local dashboard for DG extraction runs (stdlib only, localhost).

Usage:
    python3 scripts/dg_dashboard.py [--port 8765]
Then open http://127.0.0.1:8765 in a browser. Single page, auto-refreshes
every 2s. The STOP button touches data/dg_stop (run halts after the current
record, checkpoint-safe). Binds localhost only — never exposed to a network.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGE = Path(__file__).resolve().parent / "dg_dashboard.html"


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
