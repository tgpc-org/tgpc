"""Coverage for the listed-but-unserved report.

A registration can appear in the council's summary listing while its detail
page does not exist. The portal must show "Unknown" rather than invent a
status, so the only thing worth engineering is *noticing* when that stops
being a one-off. These tests pin the reporting rule: one line and a file on a
normal run, an email only when the volume says the listing and the detail
backend have diverged.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tgpc import enrichment


class ReportNoDetailsTests(unittest.TestCase):
    def _run(self, regs, candidates, **kwargs):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        target = Path(temp) / "no_details_at_source.json"
        alerted = enrichment.report_no_details(regs, candidates, path=target, **kwargs)
        payload = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        return alerted, payload

    def test_writes_the_list_and_stays_quiet_for_one_orphan(self):
        with patch("tgpc.enrichment.send_alert") as send:
            alerted, payload = self._run(["TG082335"], 1200)
        self.assertFalse(alerted)
        send.assert_not_called()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["candidates"], 1200)
        self.assertEqual(payload["registrations"], ["TG082335"])
        self.assertIn("ts", payload)

    def test_deduplicates_and_sorts(self):
        _, payload = self._run(["TG9", "TG1", "TG9"], 10)
        self.assertEqual(payload["registrations"], ["TG1", "TG9"])
        self.assertEqual(payload["count"], 2)

    def test_empty_list_still_writes_a_clean_file(self):
        alerted, payload = self._run([], 40)
        self.assertFalse(alerted)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["registrations"], [])

    def test_alerts_above_the_count_threshold(self):
        with patch("tgpc.enrichment.send_alert", return_value=True) as send:
            alerted, _ = self._run([f"TG{i:06d}" for i in range(6)], 1000)
        self.assertTrue(alerted)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertIn("6 records missing at source", kwargs["subject"])
        self.assertIn("warning, not a failed run", kwargs["body"])

    def test_single_orphan_in_a_tiny_run_is_not_a_percentage_alarm(self):
        # 1 of 1 is 100%, which means nothing. The share rule must not fire.
        with patch("tgpc.enrichment.send_alert", return_value=True) as send:
            alerted, _ = self._run(["TG082335"], 1)
        self.assertFalse(alerted)
        send.assert_not_called()

    def test_share_rule_fires_for_a_large_sample_under_the_count_bar(self):
        # 5 of 200 = 2.5%: below the count bar (not > 5), above the share bar.
        with patch("tgpc.enrichment.send_alert", return_value=True) as send:
            alerted, _ = self._run(["TG1", "TG2", "TG3", "TG4", "TG5"], 200)
        self.assertTrue(alerted)
        send.assert_called_once()

    def test_alert_can_be_suppressed(self):
        with patch("tgpc.enrichment.send_alert", return_value=True) as send:
            alerted, _ = self._run([f"TG{i}" for i in range(10)], 1000, alert=False)
        self.assertFalse(alerted)
        send.assert_not_called()

    def test_unwritable_path_does_not_raise(self):
        # Reporting must never fail an enrichment run that otherwise succeeded.
        target = Path("/proc/definitely/not/writable/no_details.json")
        self.assertFalse(enrichment.report_no_details(["TG1"], 10, path=target, alert=False))

    def test_path_follows_the_configured_data_directory(self):
        manager = MagicMock()
        manager.config.data_directory = "/tmp/some-data-dir"
        self.assertEqual(
            enrichment._no_details_path(manager),
            Path("/tmp/some-data-dir") / "no_details_at_source.json",
        )


class CollectorTests(unittest.TestCase):
    def test_process_records_collects_registrations_without_details(self):
        manager = MagicMock()
        manager.scraper.extract_detailed_info.side_effect = lambda reg, img: None
        collected: list[str] = []
        records = [
            MagicMock(registration_number="TG1", serial_number=1),
            MagicMock(registration_number="TG2", serial_number=2),
        ]

        with tempfile.TemporaryDirectory() as temp:
            processed = enrichment.process_records_sequential(manager, records, {}, Path(temp), no_details=collected)

        self.assertEqual(processed, 0)
        self.assertEqual(collected, ["TG1", "TG2"])

    def test_collector_is_optional(self):
        manager = MagicMock()
        manager.scraper.extract_detailed_info.return_value = None
        with tempfile.TemporaryDirectory() as temp:
            processed = enrichment.process_records_sequential(
                manager, [MagicMock(registration_number="TG1", serial_number=1)], {}, Path(temp)
            )
        self.assertEqual(processed, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
