"""Tests for the local run record and the failure alert.

The point of this module is that a failed run can no longer be silent: it
writes `data/last_run.json`, alerts once, and exits non-zero. The scheduler
also depends on `ran_successfully_today()` to make the retry slots free, so
that predicate has to be exact about the day boundary.

No network: the egress lookup and the Resend call are both injected/patched.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from tgpc import run_status

IST = timezone(timedelta(hours=5, minutes=30))


class RunStatusFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "last_run.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_then_read_roundtrip(self):
        record = run_status.write_run_status("updated", duration_s=41.2, egress="49.37.155.92", path=self.path)
        self.assertEqual(record["status"], "updated")
        self.assertEqual(run_status.read_run_status(self.path), record)
        self.assertEqual(run_status.read_run_status(self.path)["egress_ip"], "49.37.155.92")

    def test_write_is_atomic_and_leaves_no_temp_file(self):
        run_status.write_run_status("blocked", egress="", path=self.path)
        leftovers = [p.name for p in Path(self.tmp.name).iterdir() if p.name != "last_run.json"]
        self.assertEqual(leftovers, [])

    def test_read_missing_file_returns_none(self):
        self.assertIsNone(run_status.read_run_status(self.path))

    def test_read_corrupt_file_returns_none(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(run_status.read_run_status(self.path))

    def test_write_failure_never_raises(self):
        # An unwritable target (a directory) must not fail the run.
        bad = Path(self.tmp.name) / "sub"
        bad.mkdir()
        record = run_status.write_run_status("updated", egress="", path=bad)
        self.assertEqual(record["status"], "updated")

    def test_records_the_isp_address_separately_from_the_tunnel(self):
        # WARP is up during a run, so the address the council saw is
        # Cloudflare's; the ISP lease is sampled before the tunnel is raised.
        record = run_status.write_run_status(
            "blocked", egress="104.28.230.228", host_egress="49.37.155.92", path=self.path
        )
        self.assertEqual(record["egress_ip"], "104.28.230.228")
        self.assertEqual(record["host_ip"], "49.37.155.92")
        self.assertIn("49.37.155.92", run_status._alert_body(record))


class RanSuccessfullyTodayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "last_run.json"
        self.now = datetime(2026, 11, 10, 18, 5, tzinfo=IST)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, status, ts):
        self.path.write_text(json.dumps({"status": status, "ts": ts}), encoding="utf-8")

    def test_true_when_today_succeeded(self):
        self._write("updated", self.now.isoformat(timespec="seconds"))
        self.assertTrue(run_status.ran_successfully_today(self.path, now=self.now))

    def test_false_when_today_failed(self):
        self._write("blocked", self.now.isoformat(timespec="seconds"))
        self.assertFalse(run_status.ran_successfully_today(self.path, now=self.now))

    def test_false_when_yesterday_succeeded(self):
        yesterday = (self.now - timedelta(days=1)).isoformat(timespec="seconds")
        self._write("updated", yesterday)
        self.assertFalse(run_status.ran_successfully_today(self.path, now=self.now))

    def test_false_for_missing_or_garbage(self):
        self.assertFalse(run_status.ran_successfully_today(self.path, now=self.now))
        self.path.write_text(json.dumps({"status": "updated", "ts": "not-a-date"}), encoding="utf-8")
        self.assertFalse(run_status.ran_successfully_today(self.path, now=self.now))

    def test_naive_timestamp_is_read_as_ist(self):
        naive = self.now.replace(tzinfo=None).isoformat(timespec="seconds")
        self._write("updated", naive)
        self.assertTrue(run_status.ran_successfully_today(self.path, now=self.now))


class AlertTests(unittest.TestCase):
    RECORD = {
        "status": "blocked",
        "error": "source preflight failed",
        "ts": "2026-11-10T06:02:11+05:30",
        "egress_ip": "49.37.155.92",
    }

    def test_skipped_without_credentials(self):
        post = MagicMock()
        self.assertFalse(run_status.send_alert(self.RECORD, api_key="", recipient="", post=post))
        post.assert_not_called()

    def test_posts_one_alert_to_resend(self):
        post = MagicMock(return_value=MagicMock(status_code=200))
        ok = run_status.send_alert(self.RECORD, api_key="re_test", recipient="ops@example.com", post=post)
        self.assertTrue(ok)
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.resend.com/emails")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer re_test")
        self.assertEqual(kwargs["json"]["to"], ["ops@example.com"])
        self.assertIn("blocked", kwargs["json"]["subject"])
        self.assertIn("49.37.155.92", kwargs["json"]["text"])

    def test_alert_carries_no_record_lists(self):
        # Standing rule: records emails use the default make-scrape format.
        # An operational alert must never look like a records report.
        body = run_status._alert_body(self.RECORD)
        for field in ("new_details", "modified_details", "removed_details"):
            self.assertNotIn(field, body)

    def test_subject_and_body_can_be_overridden(self):
        # Non-fatal warnings (enrichment.report_no_details) reuse this
        # transport without claiming the scrape failed.
        post = MagicMock(return_value=MagicMock(status_code=200))
        ok = run_status.send_alert(
            {"status": "enrichment_no_details"},
            api_key="re_test",
            recipient="ops@example.com",
            post=post,
            subject="TGPC enrichment: 6 records missing at source",
            body="not a failed run",
        )
        self.assertTrue(ok)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["subject"], "TGPC enrichment: 6 records missing at source")
        self.assertEqual(payload["text"], "not a failed run")

    def test_http_error_is_reported_but_never_raised(self):
        post = MagicMock(return_value=MagicMock(status_code=422))
        self.assertFalse(run_status.send_alert(self.RECORD, api_key="re_test", recipient="a@b.c", post=post))

    def test_transport_error_is_swallowed(self):
        post = MagicMock(side_effect=RuntimeError("network down"))
        self.assertFalse(run_status.send_alert(self.RECORD, api_key="re_test", recipient="a@b.c", post=post))


class EgressIpTests(unittest.TestCase):
    def test_returns_empty_string_on_failure(self):
        with unittest.mock.patch("tgpc.run_status.requests.get", side_effect=RuntimeError("nope")):
            self.assertEqual(run_status.egress_ip(), "")

    def test_rejects_a_non_ip_body(self):
        with unittest.mock.patch(
            "tgpc.run_status.requests.get",
            return_value=MagicMock(text="<html>captive portal</html>"),
        ):
            self.assertEqual(run_status.egress_ip(), "")

    def test_returns_a_valid_ip(self):
        with unittest.mock.patch("tgpc.run_status.requests.get", return_value=MagicMock(text="49.37.155.92\n")):
            self.assertEqual(run_status.egress_ip(), "49.37.155.92")


if __name__ == "__main__":
    unittest.main()
