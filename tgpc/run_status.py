"""Machine-readable record of the last pipeline run.

A blocked or unavailable source used to leave no trace outside GitHub Actions:
`Manager._write_update_outputs` only writes `$GITHUB_OUTPUT`, so a scheduled
run on the Mac logged one line and exited 0. Nothing could tell "the source
refused us" apart from "there were no changes", and the site simply went stale.

This module is the local equivalent of that channel:

- `write_run_status()` writes `data/last_run.json` on every run (status, time,
  egress IP, duration). The egress IP is also the cheapest way to learn how
  often the ISP rotates it.
- `ran_successfully_today()` is what makes the 10:00/14:00/18:00 retries free:
  if the day already succeeded, the scheduler exits without a 76 MB download.
- `send_alert()` emails one short operational alert when a run did not
  succeed. It is not a records report (those always use the default
  `sync_to_email` format); it carries no record lists.

Nothing here raises: a failure to record or alert must never mask the run's
real outcome.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from tgpc.utils import setup_logging

logger = setup_logging("tgpc.run_status")

IST = timezone(timedelta(hours=5, minutes=30))

STATUS_PATH = Path("data") / "last_run.json"

# The only status that means "the pipeline did what it exists to do".
SUCCESS_STATUS = "updated"

# Third-party, tiny, and not the council: asking the source for our own IP
# would be blocked exactly when we need the number.
IP_URL = "https://api.ipify.org"
IP_TIMEOUT = 4

RESEND_URL = "https://api.resend.com/emails"
RESEND_FROM = "TGPC Pipeline <onboarding@resend.dev>"


def _now_ist() -> datetime:
    return datetime.now(IST)


def egress_ip(timeout: int = IP_TIMEOUT) -> str:
    """Best-effort public egress IP. Returns "" on any failure."""
    try:
        r = requests.get(IP_URL, timeout=timeout)
        text = (r.text or "").strip()
        ipaddress.ip_address(text)  # validates; raises on anything else
        return text
    except Exception:  # noqa: BLE001 - recording the IP must never fail a run
        return ""


def write_run_status(
    status: str,
    *,
    error: str = "",
    duration_s: float | None = None,
    egress: str | None = None,
    host_egress: str = "",
    command: str = "scrape",
    path: Path | None = None,
) -> dict[str, Any]:
    """Write the run record atomically and return it.

    Two addresses are recorded because they answer different questions:

    - `egress_ip` — the address the run actually used. When a tunnel (WARP) is
      up this is the tunnel's, i.e. exactly the address the council refused,
      which is what a block needs explained.
    - `host_ip` — the machine's own address, sampled before any tunnel is
      raised. This is the ISP lease, so it is the one that answers "how often
      does my IP change?".
    """
    record: dict[str, Any] = {
        "ts": _now_ist().isoformat(timespec="seconds"),
        "command": command,
        "status": status,
        "error": error,
        "egress_ip": egress if egress is not None else egress_ip(),
        "host_ip": host_egress,
        "duration_s": duration_s,
    }
    target = Path(path) if path is not None else STATUS_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except OSError as e:  # noqa: BLE001 - never fail a run over bookkeeping
        logger.warning("Could not write %s: %s", target, e)
    return record


def read_run_status(path: Path | None = None) -> dict[str, Any] | None:
    """Read the last run record, or None when absent/unreadable."""
    target = Path(path) if path is not None else STATUS_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def ran_successfully_today(path: Path | None = None, now: datetime | None = None) -> bool:
    """True when the last recorded run succeeded and happened today (IST).

    The scheduler uses this to make the retry slots a no-op once the day is
    already done, instead of downloading the 76 MB table four times.
    """
    record = read_run_status(path)
    if not record or record.get("status") != SUCCESS_STATUS:
        return False
    try:
        stamp = datetime.fromisoformat(str(record.get("ts", "")))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    today = (now or _now_ist()).astimezone(IST).date()
    return stamp.astimezone(IST).date() == today


def _alert_body(record: dict[str, Any]) -> str:
    status = record.get("status", "unknown")
    when = record.get("ts", "")
    try:
        when = datetime.fromisoformat(str(when)).astimezone(IST).strftime("%d %b %Y, %H:%M IST")
    except ValueError:
        pass
    return (
        "The scheduled TGPC scrape did not succeed.\n"
        "\n"
        f"  Status:   {status}\n"
        f"  Detail:   {record.get('error') or 'see data/logs/latest-scrape.log'}\n"
        f"  When:     {when}\n"
        f"  Egress:   {record.get('egress_ip') or 'unknown'}\n"
        f"  Home IP:  {record.get('host_ip') or 'unknown'}\n"
        "\n"
        "Existing data was preserved and nothing was synced, so the portal is\n"
        "still serving the last good snapshot (it shows LAST SYNC on the site and\n"
        "/api/health reports `degraded` once that is 48h old).\n"
        "\n"
        "What happens next, automatically:\n"
        "  The scrape retries at 10:00, 14:00 and 18:00 IST and skips the run\n"
        "  entirely once a day has succeeded.\n"
        "\n"
        "If all four slots fail, the source is refusing this IP. Switch egress:\n"
        "  1. Tether to a phone hotspot (different Indian ASN) and run\n"
        "     `make scrape`.\n"
        "  2. Or set TGPC_PROXY_URL and run `make scrape` through that proxy.\n"
        '  See DESIGN.md -> "When the source blocks this host".\n'
    )


def send_alert(
    record: dict[str, Any],
    *,
    api_key: str | None = None,
    recipient: str | None = None,
    post: Callable[..., Any] | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> bool:
    """Email one operational alert via Resend. Never raises.

    Returns True only when Resend accepted the message. Missing credentials are
    a skip, not an error: the status file and the exit code still carry the
    outcome, and `make scrape` must not fail because alerting is unconfigured.

    `subject`/`body` override the scrape-failure text so a non-fatal warning
    (see `enrichment.report_no_details`) can reuse the same transport without
    pretending the run failed.
    """
    key = api_key if api_key is not None else os.environ.get("RESEND_API_KEY", "")
    to = recipient if recipient is not None else os.environ.get("NOTIFICATION_EMAIL", "")
    if not key or not to:
        logger.info("Alert not sent — RESEND_API_KEY / NOTIFICATION_EMAIL not configured")
        return False

    status = str(record.get("status", "unknown")).replace("_", " ")
    payload = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": subject or f"TGPC scrape {status} — {_now_ist().strftime('%d %b %Y')}",
        "text": body if body is not None else _alert_body(record),
    }
    try:
        sender = post or requests.post
        r = sender(
            RESEND_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        ok = getattr(r, "status_code", 0) < 400
        if not ok:
            logger.warning("Alert email rejected: HTTP %s", getattr(r, "status_code", "?"))
        return ok
    except Exception as e:  # noqa: BLE001 - alerting must never mask the outcome
        logger.warning("Alert email failed: %s", e)
        return False


def _main(argv: list[str]) -> int:
    args = set(argv[1:])
    if "--check-today" in args:
        ok = ran_successfully_today()
        print("succeeded today" if ok else "not done today")
        return 0 if ok else 1
    record = read_run_status()
    if record is None:
        print("no run recorded", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shim
    sys.exit(_main(sys.argv))


__all__ = [
    "IST",
    "STATUS_PATH",
    "SUCCESS_STATUS",
    "egress_ip",
    "write_run_status",
    "read_run_status",
    "ran_successfully_today",
    "send_alert",
]
