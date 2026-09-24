"""Tests for scripts/check_health.py (production freshness monitor).

The monitor is stdlib-only and its core, evaluate(), is pure — so most
tests exercise classification directly. Network paths are mocked; no test
touches the live site.
"""

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")

from scripts.check_health import DOWN, HEALTHY, STALE, USAGE, evaluate, main  # noqa: E402


def payload(hours_ago=5, supabase="ok", status="ok"):
    return {
        "status": status,
        "checks": {
            "supabase": {"status": supabase},
            "last_sync": {"status": "ok", "hours_ago": hours_ago},
        },
    }


class EvaluateTests(unittest.TestCase):
    def test_fresh_within_threshold(self):
        code, _ = evaluate(payload(hours_ago=5), 48.0)
        self.assertEqual(code, HEALTHY)

    def test_stale_over_threshold(self):
        code, msg = evaluate(payload(hours_ago=50), 48.0)
        self.assertEqual(code, STALE)
        self.assertIn("50", msg)

    def test_boundary_equal_is_healthy(self):
        code, _ = evaluate(payload(hours_ago=48), 48.0)
        self.assertEqual(code, HEALTHY)

    def test_missing_hours_ago_is_down_not_fresh(self):
        p = payload()
        del p["checks"]["last_sync"]["hours_ago"]
        code, _ = evaluate(p, 48.0)
        self.assertEqual(code, DOWN)

    def test_non_numeric_hours_ago_is_down(self):
        code, _ = evaluate(payload(hours_ago="recently"), 48.0)
        self.assertEqual(code, DOWN)

    def test_bool_hours_ago_is_down(self):
        # bool is an int subclass — must not pass as 0/1 hours.
        code, _ = evaluate(payload(hours_ago=True), 48.0)
        self.assertEqual(code, DOWN)

    def test_endpoint_down_overrides_fresh_data(self):
        code, _ = evaluate(payload(hours_ago=1, status="down"), 48.0)
        self.assertEqual(code, DOWN)

    def test_missing_checks_object_is_down(self):
        code, _ = evaluate({"status": "ok"}, 48.0)
        self.assertEqual(code, DOWN)

    def test_supabase_failure_is_down_not_stale(self):
        code, msg = evaluate(payload(hours_ago=500, supabase="error"), 48.0)
        self.assertEqual(code, DOWN)
        self.assertIn("supabase", msg)

    def test_missing_supabase_check_is_down(self):
        p = payload()
        del p["checks"]["supabase"]
        code, _ = evaluate(p, 48.0)
        self.assertEqual(code, DOWN)

    def test_non_dict_payload_is_down(self):
        code, _ = evaluate(["ok"], 48.0)
        self.assertEqual(code, DOWN)


class MainTests(unittest.TestCase):
    def _write(self, tmp, obj):
        p = Path(tmp) / "health.json"
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def test_from_file_healthy_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, payload(hours_ago=5))
            self.assertEqual(main(["--from-file", str(p)]), 0)

    def test_from_file_stale_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(tmp, payload(hours_ago=99))
            self.assertEqual(main(["--from-file", str(p), "--max-hours", "48"]), STALE)

    def test_from_file_unreadable_exit_3(self):
        self.assertEqual(main(["--from-file", "/nonexistent/health.json"]), USAGE)

    def test_unreachable_exit_2(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionError("no route")):
            self.assertEqual(main(["--url", "https://example.invalid/api/health"]), DOWN)

    def test_default_url_is_health_endpoint(self):
        from scripts import check_health

        self.assertTrue(check_health.DEFAULT_URL.endswith("/api/health"))

    def test_http_503_body_still_classified(self):
        body = json.dumps(payload(hours_ago=99)).encode()
        err = urllib.error.HTTPError("https://x/", 503, "Down", {}, None)
        err.read = lambda: body  # type: ignore[method-assign]
        with patch("urllib.request.urlopen", side_effect=err):
            # 503 with a stale body classifies on the body, not the status.
            self.assertEqual(main(["--url", "https://example.invalid/api/health"]), STALE)


if __name__ == "__main__":
    unittest.main()
