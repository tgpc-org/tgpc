import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, ".")

from scripts.dg_dashboard import build_status, tail_lines, to_ist_day  # noqa: E402


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

    def test_plain_language_labels(self):
        from scripts.dg_dashboard import PAGE

        html = PAGE.read_text(encoding="utf-8")
        for needle in ("Saved", "Couldn't fetch", "Held for review", "In cloud database", "Finished: ", "plainReason"):
            self.assertIn(needle, html)
        for gone in ("checkpoint:", " terminal'"):
            self.assertNotIn(gone, html)

    def test_theme_toggle_wired(self):
        from scripts.dg_dashboard import PAGE

        html = PAGE.read_text(encoding="utf-8")
        for needle in ('id="theme-toggle"', "toggleTheme()", "dg-theme", 'data-theme="dark"', "prefers-color-scheme"):
            self.assertIn(needle, html)

    def test_start_button_present(self):
        from scripts.dg_dashboard import PAGE

        html = PAGE.read_text(encoding="utf-8")
        for needle in ('id="start"', "startRun()", 'id="count"', "/api/start"):
            self.assertIn(needle, html)

    def test_next_ids_skips_done_and_terminal_retries_failed(self):
        import tempfile

        from scripts.dg_dashboard import next_ids, run_active

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rph = d / "rph.json"
            rph.write_text(
                json.dumps(
                    [
                        {"registration_number": "R1", "serial_number": 1},
                        {"registration_number": "R2", "serial_number": 2},
                        {"registration_number": "R3", "serial_number": 3},
                        {"registration_number": "R4", "serial_number": 4},
                    ]
                )
            )
            (d / "dg_fetch_checkpoint.json").write_text(
                json.dumps(
                    {
                        "completed": ["R1"],
                        "failed": {"R2": "timeout", "R3": "timeout"},
                        "failed_terminal": {"R3": "auth"},
                        "quarantined": [],
                    }
                )
            )
            # R2 retryable first, then fresh R4; R1 done, R3 terminal skipped
            self.assertEqual(next_ids(d, 10, rph_path=rph), ["R2", "R4"])
            self.assertEqual(next_ids(d, 1, rph_path=rph), ["R2"])
            self.assertFalse(run_active(d))
            (d / "dg_fetch.pid").write_text(str(os.getpid()))
            self.assertTrue(run_active(d))

    def test_zombie_pid_counts_as_inactive(self):
        import subprocess as sp

        from scripts.dg_dashboard import run_active

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Real finished child, deliberately unreaped: a true zombie, exactly
            # the server's situation after a button-launched run exits.
            proc = sp.Popen(["true"])
            import time as _time

            _time.sleep(0.5)  # `true` exits in ms; no poll()/wait() so it stays a zombie
            (d / "dg_fetch.pid").write_text(str(proc.pid))
            try:
                self.assertFalse(run_active(d))
                # ...and the stale pidfile is cleaned so a later run can start
                self.assertFalse((d / "dg_fetch.pid").exists())
            finally:
                proc.wait()  # tidy up the zombie ourselves
            # Live process still reads active
            live = sp.Popen(["sleep", "30"])
            try:
                (d / "dg_fetch.pid").write_text(str(live.pid))
                self.assertTrue(run_active(d))
            finally:
                live.kill()
                live.wait()

    def test_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dg_fetch.log"
            p.write_text("\n".join(f"line {i}" for i in range(50)))
            tail = tail_lines(p, 30)
            self.assertEqual(len(tail), 30)
            self.assertEqual(tail[0], "line 20")
            self.assertEqual(tail_lines(Path(tmp) / "missing.log"), [])

    def test_ist_day_format(self):
        self.assertEqual(to_ist_day("2026-09-18T12:15:45Z"), "Fri-18-09-2026 17:45 IST")
        self.assertEqual(to_ist_day(""), "")
        self.assertEqual(to_ist_day("garbage"), "garbage")

    def test_next_up_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "dg_live.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "current_reg": "TS1",
                        "serial_number": 10,
                        "next_reg": "TS2",
                        "next_serial": 11,
                        "remaining_in_batch": 42,
                    }
                )
            )
            s = build_status(d)
            self.assertEqual(s["next_reg"], "TS2")
            self.assertEqual(s["next_serial"], 11)
            self.assertEqual(s["remaining_in_batch"], 42)

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
