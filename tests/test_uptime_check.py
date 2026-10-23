import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, ".")

from scripts.uptime_check import (  # noqa: E402
    STATE_DEGRADED,
    STATE_DOWN,
    STATE_OK,
    classify,
    describe_checks,
    issue_body,
    issue_title,
    probe,
    recovery_body,
)

HEALTHY = {
    "status": STATE_OK,
    "checks": {
        "supabase": {"status": "ok", "latency_ms": 41},
        "last_sync": {"status": "ok", "value": "2026-10-22T11:09:34.000Z", "hours_ago": 19.6},
    },
}

DOWN = {
    "status": STATE_DOWN,
    "checks": {
        "supabase": {"status": "down", "error": "connect timeout"},
        "last_sync": {"status": "down"},
    },
}

STALE = {
    "status": STATE_DEGRADED,
    "checks": {
        "supabase": {"status": "ok", "latency_ms": 52},
        "last_sync": {"status": "stale", "value": "2026-10-20T00:00:00.000Z", "hours_ago": 52.4},
    },
}


class UptimeClassifyTests(unittest.TestCase):
    def test_healthy_response_is_ok(self):
        verdict = classify(200, HEALTHY)
        self.assertEqual(verdict.state, STATE_OK)
        self.assertTrue(verdict.healthy)
        self.assertFalse(verdict.alarming)

    def test_down_response_carries_the_failing_check(self):
        verdict = classify(503, DOWN)
        self.assertEqual(verdict.state, STATE_DOWN)
        self.assertFalse(verdict.healthy)
        self.assertTrue(verdict.alarming)
        self.assertIn("supabase=down: connect timeout", verdict.reason)
        self.assertIn("HTTP 503", verdict.reason)

    def test_stale_sync_is_degraded_and_does_not_alarm(self):
        verdict = classify(200, STALE)
        self.assertEqual(verdict.state, STATE_DEGRADED)
        self.assertFalse(verdict.healthy)
        # Manual pipeline cadence: staleness is annotated, not alarmed.
        self.assertFalse(verdict.alarming)
        self.assertIn("last_sync=stale (52.4h ago)", verdict.reason)

    def test_status_wins_over_a_cheerful_http_code(self):
        # A 200 whose body says "down" is still down; the body is authoritative.
        self.assertEqual(classify(200, DOWN).state, STATE_DOWN)

    def test_non_json_body_is_down(self):
        verdict = classify(200, None)
        self.assertEqual(verdict.state, STATE_DOWN)
        self.assertIn("no health JSON (HTTP 200)", verdict.reason)

    def test_unreachable_endpoint_is_down(self):
        verdict = classify(0, {"error": "timed out"})
        self.assertEqual(verdict.state, STATE_DOWN)
        self.assertIn("HTTP n/a", verdict.reason)
        self.assertIn("timed out", verdict.reason)

    def test_unknown_status_is_down(self):
        verdict = classify(200, {"status": "banana", "checks": {}})
        self.assertEqual(verdict.state, STATE_DOWN)
        self.assertIn("banana", verdict.reason)

    def test_describe_checks_ignores_healthy_entries(self):
        self.assertEqual(describe_checks(HEALTHY["checks"]), "")
        self.assertEqual(describe_checks(None), "")
        self.assertEqual(describe_checks({"supabase": {"status": "ok"}}), "")


class UptimeIssueTextTests(unittest.TestCase):
    def test_title_is_stable_per_host_and_state(self):
        down = classify(503, DOWN)
        stale = classify(200, STALE)
        self.assertEqual(issue_title(down, "https://tgpc.pages.dev/api/health"), "Uptime: tgpc.pages.dev is down")
        self.assertEqual(issue_title(stale, "https://tgpc.pages.dev/api/health"), "Uptime: tgpc.pages.dev is degraded")

    def test_failure_body_names_the_endpoint_reason_and_run(self):
        verdict = classify(503, DOWN)
        body = issue_body(verdict, "https://tgpc.pages.dev/api/health", "2026-10-23T06:00:00Z", "https://x/run/1")
        self.assertIn("- **Endpoint:** https://tgpc.pages.dev/api/health", body)
        self.assertIn("- **State:** `down`", body)
        self.assertIn("supabase=down", body)
        self.assertIn("https://x/run/1", body)

    def test_recovery_body_has_no_failure_reason(self):
        body = recovery_body("https://tgpc.pages.dev/api/health", "2026-10-23T07:00:00Z")
        self.assertIn("Recovered", body)
        self.assertNotIn("Reason", body)


class _Handler(BaseHTTPRequestHandler):
    status = 200
    payload = b"{}"

    def do_GET(self):  # noqa: N802 - stdlib naming
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):  # silence the test server
        pass


class UptimeProbeTests(unittest.TestCase):
    def _serve(self, status, payload):
        _Handler.status = status
        _Handler.payload = payload
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/api/health"

    def test_probe_reads_the_body_of_a_503(self):
        body = json.dumps({"status": STATE_DOWN, "checks": DOWN["checks"]}).encode()
        status, payload = probe(self._serve(503, body), 5)
        self.assertEqual(status, 503)
        verdict = classify(status, payload)
        self.assertEqual(verdict.state, STATE_DOWN)
        self.assertIn("supabase=down: connect timeout", verdict.reason)

    def test_probe_reports_a_closed_port_instead_of_raising(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        status, payload = probe(f"http://127.0.0.1:{port}/api/health", 2)
        self.assertEqual(status, 0)
        self.assertEqual(classify(status, payload).state, STATE_DOWN)


if __name__ == "__main__":
    unittest.main()
