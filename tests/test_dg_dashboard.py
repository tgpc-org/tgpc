import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, ".")

from scripts.dg_dashboard import build_status, tail_lines  # noqa: E402


class DashboardTests(unittest.TestCase):
    def test_status_merges_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "dg_live.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "current_reg": "TS1",
                        "event": "fetching",
                        "total": 10,
                        "records_per_min": 5.5,
                        "eta_mins": 1.2,
                        "updated_at": "t",
                    }
                )
            )
            (d / "dg_stats.json").write_text(
                json.dumps(
                    {
                        "done": 3,
                        "failed": 1,
                        "quarantined": 0,
                        "sb_upserted": 3,
                        "captcha_firstpass_ok": 3,
                        "captcha_retries": 0,
                        "fail_by_reason": {"DgDetailError": 1},
                    }
                )
            )
            (d / "dg_fetch_checkpoint.json").write_text(
                json.dumps(
                    {
                        "completed": ["a", "b", "c"],
                        "failed_terminal": {"x": "y"},
                    }
                )
            )
            s = build_status(d)
            self.assertEqual(s["status"], "running")
            self.assertEqual(s["current_reg"], "TS1")
            self.assertEqual(s["processed"], 4)
            self.assertEqual(s["completed"], 3)
            self.assertEqual(s["terminal"], 1)
            self.assertFalse(s["stop_armed"])

    def test_status_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = build_status(Path(tmp))
            self.assertEqual(s["status"], "idle")
            self.assertEqual(s["done"], 0)

    def test_stop_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "dg_stop").write_text("")
            self.assertTrue(build_status(Path(tmp))["stop_armed"])

    def test_theme_toggle_wired(self):
        from scripts.dg_dashboard import PAGE

        html = PAGE.read_text(encoding="utf-8")
        for needle in ('id="theme-toggle"', "toggleTheme()", "dg-theme", 'data-theme="dark"', "prefers-color-scheme"):
            self.assertIn(needle, html)

    def test_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dg_fetch.log"
            p.write_text("\n".join(f"line {i}" for i in range(50)))
            tail = tail_lines(p, 30)
            self.assertEqual(len(tail), 30)
            self.assertEqual(tail[0], "line 20")
            self.assertEqual(tail_lines(Path(tmp) / "missing.log"), [])

    def test_all_time_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rows = [
                {"registration_number": "A", "outcome": "saved"},
                {"registration_number": "B", "outcome": "failed"},
                {"registration_number": "C", "outcome": "quarantined"},
                {"registration_number": "D", "outcome": "saved"},
            ]
            (d / "dg_history.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            (d / "dg_stats.json").write_text(json.dumps({"done": 0, "failed": 0, "quarantined": 0}))
            s = build_status(d)
            self.assertEqual(s["all_time"], {"saved": 2, "failed": 1, "quarantined": 1})
            self.assertEqual(s["history_records"], 4)


if __name__ == "__main__":
    unittest.main()
