#!/usr/bin/env python3
"""Watch production data freshness via the public /api/health endpoint.

Why this exists: `ui/e2e/smoke.spec.ts` used to assert
`last_sync.hours_ago < 48` against the live site. That made every ui/ push fail
whenever the sync pipeline had not been run for two days — a data condition, not
a property of the code being pushed, and the gate went red for four days
straight. The same signal belongs in a scheduled check that alerts on its own.

On the default threshold: 48h is the endpoint's own definition of `stale`, kept
here so the signal is unchanged. The sync pipeline is workflow_dispatch-only
(there is no cron anywhere in .github/workflows), so a manual cadence will
legitimately exceed 48h and this check will alert until the data is refreshed.
Either raise --max-hours to match a deliberate cadence, or schedule the pipeline.

Stdlib only on purpose: the monitoring path must not need `pip install` to run.

Exit codes:
  0  healthy
  1  stale — last_sync older than the threshold
  2  down  — unreachable, non-200, missing freshness data, or Supabase failing
  3  bad usage or unreadable input

Usage:
  python3 scripts/check_health.py
  python3 scripts/check_health.py --max-hours 96
  python3 scripts/check_health.py --url https://preview.pages.dev
  python3 scripts/check_health.py --from-file payload.json    # offline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_URL = os.environ.get("PROD_URL", "https://tgpc.pages.dev/api/health")
DEFAULT_MAX_HOURS = 48.0
USER_AGENT = "tgpc-health-monitor/1.0 (+https://github.com/tgpc-org/tgpc)"

HEALTHY = 0
STALE = 1
DOWN = 2
USAGE = 3

LABELS = {HEALTHY: "OK", STALE: "STALE", DOWN: "DOWN", USAGE: "USAGE"}


def fetch_health(url: str, timeout: float = 30.0) -> Tuple[Optional[int], Dict[str, Any]]:
    """GET the endpoint, returning (http_status, payload).

    http_status is None when the host could not be reached at all. A non-2xx
    response is still returned with its body: the endpoint answers 503 with the
    same JSON shape when a check is down, and that detail is worth reporting.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"_raw": body[:200]}
    except Exception as e:
        return None, {"_error": str(e)}


def evaluate(payload: Dict[str, Any], max_hours: float) -> Tuple[int, str]:
    """Classify a /api/health payload. Pure, so it is directly unit-testable."""
    if not isinstance(payload, dict):
        return DOWN, "health payload is not a JSON object"

    if payload.get("status") == "down":
        return DOWN, "endpoint reports status=down"

    checks = payload.get("checks")
    if not isinstance(checks, dict):
        return DOWN, "health payload has no checks object"

    supabase = checks.get("supabase") or {}
    if supabase.get("status") != "ok":
        return DOWN, f"supabase check is {supabase.get('status', 'missing')}"

    last_sync = checks.get("last_sync") or {}
    hours_ago = last_sync.get("hours_ago")
    # bool is an int subclass, so exclude it explicitly.
    if not isinstance(hours_ago, (int, float)) or isinstance(hours_ago, bool):
        # The endpoint omits hours_ago when the metadata row is unreadable, so
        # absence means "cannot prove freshness", not "fresh".
        return DOWN, "no last_sync.hours_ago reported (metadata row missing?)"

    if hours_ago > max_hours:
        return STALE, f"last_sync is {hours_ago}h old, over the {max_hours:g}h threshold"

    return HEALTHY, f"last_sync is {hours_ago}h old, within the {max_hours:g}h threshold"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Alert on stale or broken production health.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"health endpoint (default: {DEFAULT_URL})")
    parser.add_argument("--from-file", help="evaluate a saved payload instead of calling the endpoint")
    parser.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS, help="staleness threshold in hours")
    parser.add_argument("--timeout", type=float, default=30.0, help="request timeout in seconds")
    args = parser.parse_args(argv)

    source = args.from_file or args.url

    if args.from_file:
        try:
            payload: Any = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"USAGE — cannot read {args.from_file}: {e}")
            return USAGE
        status: Optional[int] = 200
    else:
        status, payload = fetch_health(args.url, args.timeout)
        if status is None:
            print(f"DOWN — {args.url}: {payload.get('_error', 'unknown error')}")
            return DOWN

    code, message = evaluate(payload, args.max_hours)
    print(f"{LABELS.get(code, '?')} — {source} (HTTP {status}): {message}")
    return code


if __name__ == "__main__":
    sys.exit(main())
