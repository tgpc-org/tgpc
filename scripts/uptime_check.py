"""Availability probe for the public portal (stdlib only).

Hits `/api/health`, classifies the answer, and exits non-zero when the portal is
not healthy. `.github/workflows/uptime.yml` runs this every 30 minutes and keeps
one open `uptime` issue while it fails, closing it when the check passes again.

Usage:
    python3 scripts/uptime_check.py [--url URL] [--timeout SECONDS]

Writes `state`, `reason`, `title`, `body` and `recovery_body` to $GITHUB_OUTPUT
when that variable is set, so the workflow can hand them straight to `gh`.

Why this and not an external monitor: the health endpoint is the only signal
that covers both halves of "is the portal working" - Supabase reachability and
whether the registry sync is still running - and it is already public and
rate-limited, so a GitHub runner polling it twice an hour costs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

STATE_OK = "ok"
STATE_DEGRADED = "degraded"
STATE_DOWN = "down"

DEFAULT_URL = "https://tgpc.pages.dev/api/health"

# The health document is a few hundred bytes; refuse anything that pretends
# otherwise rather than reading an unbounded body from a stranger.
MAX_BODY = 64_000


@dataclass(frozen=True)
class Verdict:
    """Outcome of one probe: a state plus a one-line human reason."""

    state: str
    reason: str

    @property
    def healthy(self) -> bool:
        return self.state == STATE_OK

    @property
    def alarming(self) -> bool:
        """Whether this verdict should fail the run and open an issue.

        Only a hard `down` does. `degraded` means `last_sync` is older than the
        health endpoint's 48h threshold - and the registry is refreshed by a
        manual pipeline run, so a stale window is expected between runs. A
        30-minute alarm on it would repeat the noise that got the 2026-10
        freshness poll removed; the workflow annotates it instead. Availability
        is different: nobody is watching that.
        """
        return self.state == STATE_DOWN


def describe_checks(checks: object) -> str:
    """Summarise the failing checks, e.g. `last_sync=stale (52.3h ago)`."""
    if not isinstance(checks, dict):
        return ""

    problems = []
    for name, check in checks.items():
        if not isinstance(check, dict) or check.get("status") == "ok":
            continue
        detail = ""
        if check.get("error"):
            detail = f": {check['error']}"
        elif check.get("hours_ago") is not None:
            detail = f" ({check['hours_ago']}h ago)"
        problems.append(f"{name}={check.get('status', 'unknown')}{detail}")
    return "; ".join(problems)


def classify(http_status: int, payload: object) -> Verdict:
    """Turn one health response into a verdict.

    The endpoint answers 503 *with* a JSON body when it is down, so the body is
    authoritative when it parses and the status code is only the fallback.
    """
    if not isinstance(payload, dict) or "status" not in payload:
        detail = ""
        if isinstance(payload, dict) and payload.get("error"):
            detail = f": {payload['error']}"
        code = http_status or "n/a"
        return Verdict(STATE_DOWN, f"no health JSON (HTTP {code}){detail}")

    reported = payload.get("status")
    problems = describe_checks(payload.get("checks"))
    suffix = f"; {problems}" if problems else ""

    if http_status == 200 and reported == STATE_OK:
        return Verdict(STATE_OK, "HTTP 200, status=ok")
    if reported == STATE_DEGRADED:
        return Verdict(STATE_DEGRADED, f"HTTP {http_status}, status=degraded{suffix}")
    return Verdict(STATE_DOWN, f"HTTP {http_status}, status={reported!r}{suffix}")


def decode(raw: bytes) -> object:
    """Decoded JSON, or None when the body is not JSON at all."""
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


def probe(url: str, timeout: float) -> tuple[int, object]:
    """Fetch one health response as (http status, decoded body or None)."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "tgpc-uptime/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, decode(response.read(MAX_BODY))
    except urllib.error.HTTPError as error:
        # A failing health check still answers with JSON; that body is the
        # useful half of the alert, so do not let the status code discard it.
        with error:
            return error.code, decode(error.read(MAX_BODY))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return 0, {"error": str(reason)}


def endpoint_host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def issue_title(verdict: Verdict, url: str) -> str:
    """Stable title: one issue per host, reused across failures."""
    state = "degraded" if verdict.state == STATE_DEGRADED else "down"
    return f"Uptime: {endpoint_host(url)} is {state}"


def issue_body(verdict: Verdict, url: str, checked_at: str, run_url: str = "") -> str:
    lines = [
        "The scheduled uptime check failed.",
        "",
        f"- **Endpoint:** {url}",
        f"- **State:** `{verdict.state}`",
        f"- **Reason:** {verdict.reason}",
        f"- **Checked at:** {checked_at}",
    ]
    if run_url:
        lines.append(f"- **Workflow run:** {run_url}")
    lines += [
        "",
        "This issue is reused: later failures are added as comments, and it is "
        "closed automatically once `/api/health` reports `ok` again.",
    ]
    return "\n".join(lines)


def recovery_body(url: str, checked_at: str, run_url: str = "") -> str:
    lines = [
        "Recovered: the health check passes again.",
        "",
        f"- **Endpoint:** {url}",
        f"- **Checked at:** {checked_at}",
    ]
    if run_url:
        lines.append(f"- **Workflow run:** {run_url}")
    return "\n".join(lines)


def current_run_url() -> str:
    """Link back to this workflow run when running inside GitHub Actions."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def write_output(name: str, value: str) -> None:
    """Append one step output, using the heredoc form for multi-line values."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        if "\n" not in value:
            handle.write(f"{name}={value}\n")
            return
        delimiter = f"EOF_{uuid4().hex}"
        handle.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the public health endpoint.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"health URL (default: {DEFAULT_URL})")
    parser.add_argument("--timeout", type=float, default=20.0, help="request timeout in seconds (default: 20)")
    args = parser.parse_args(argv)

    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status, payload = probe(args.url, args.timeout)
    verdict = classify(status, payload)
    run_url = current_run_url()

    print(f"{verdict.state}: {verdict.reason}")
    write_output("state", verdict.state)
    write_output("reason", verdict.reason)
    write_output("recovery_body", recovery_body(args.url, checked_at, run_url))
    if verdict.alarming:
        # The failure text is only meaningful while failing; a passing run
        # writes the recovery comment instead.
        write_output("title", issue_title(verdict, args.url))
        write_output("body", issue_body(verdict, args.url, checked_at, run_url))
    return 0 if not verdict.alarming else 1


if __name__ == "__main__":
    raise SystemExit(main())
