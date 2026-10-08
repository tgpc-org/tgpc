import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, ".")

from scripts.dg_dashboard import build_status, start_guards, tail_lines, to_ist_day  # noqa: E402


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
            self.assertEqual(s["all_time"], {"completed": 3, "refused": 1})
            self.assertEqual(s["resolved"], 4)
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
        for needle in ("How far along are we?", "Contact details saved", "Finished: ", "plainReason", 'id="state"'):
            self.assertIn(needle, html)
        for gone in ("checkpoint:", " terminal'", "Held for review", 'id="quar"', "In cloud database", 'id="sb"'):
            self.assertNotIn(gone, html)

    def test_theme_toggle_wired(self):
        from scripts.dg_dashboard import PAGE

        html = PAGE.read_text(encoding="utf-8")
        for needle in ('id="theme-toggle"', "toggleTheme()", "dg-theme", 'data-theme="dark"', "prefers-color-scheme"):
            self.assertIn(needle, html)

    def test_vps_controls_present(self):
        from scripts.dg_dashboard import PAGE

        html = PAGE.read_text(encoding="utf-8")
        for needle in (
            'id="vps-start"',
            'id="vps-stop"',
            'id="vps-resume"',
            'id="vps-restart"',
            'id="vps-excl"',
            'id="vps-vm-input"',
            'id="vps-vm-save"',
            "/api/vm",
            "/api/vps/resume",
            "/api/vps/restart",
            "/api/vps/exclusions",
            "/api/vps/launchlog",
            'id="vps-fetch-log"',
            "/api/vps/log",
        ):
            self.assertIn(needle, html)
        for gone in ('id="start"', 'id="stop"', "startRun()", "stopRun()", "/api/start"):
            self.assertNotIn(gone, html)

    def test_next_ids_skips_done_and_terminal_retries_failed(self):
        import tempfile

        from scripts.dg_dashboard import next_ids

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
                    }
                )
            )
            # R2 retryable first, then fresh R4; R1 done, R3 terminal skipped
            self.assertEqual(next_ids(d, 10, rph_path=rph), ["R2", "R4"])
            self.assertEqual(next_ids(d, 1, rph_path=rph), ["R2"])

    def test_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dg_fetch.log"
            p.write_text("\n".join(f"line {i}" for i in range(50)))
            tail = tail_lines(p, 30)
            self.assertEqual(len(tail), 30)
            self.assertEqual(tail[0], "line 20")
            self.assertEqual(tail_lines(Path(tmp) / "missing.log"), [])

    def test_write_vm_validation(self):
        import tempfile

        from scripts.dg_dashboard import STATIC_VM_HOST, VM_FILE, write_vm

        self.assertFalse(write_vm("not a host!!")["ok"])
        self.assertFalse(write_vm("user@ho st")["ok"])
        self.assertFalse(write_vm("a;b@c")["ok"])
        # bare login name completes with the static host
        with tempfile.TemporaryDirectory() as tmp:
            try:
                import scripts.dg_dashboard as dash

                dash.VM_FILE = Path(tmp) / "vm.conf"
                res = write_vm("tester")
                self.assertTrue(res["ok"])
                self.assertEqual(res["vm"], f"tester@{STATIC_VM_HOST}")
                self.assertEqual(Path(tmp, "vm.conf").read_text().strip(), f"tester@{STATIC_VM_HOST}")
                res = write_vm("other@1.2.3.4")
                self.assertTrue(res["ok"])
                self.assertEqual(res["vm"], "other@1.2.3.4")
            finally:
                dash.VM_FILE = VM_FILE

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

    def test_all_time_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "dg_fetch_checkpoint.json").write_text(
                json.dumps({"completed": ["A", "B", "C"], "failed_terminal": {"X": "y"}})
            )
            (d / "dg_stats.json").write_text(json.dumps({"done": 0, "failed": 0}))
            s = build_status(d)
            # Single source: checkpoint unique regs, never history line counts
            self.assertEqual(s["all_time"], {"completed": 3, "refused": 1})
            self.assertEqual(s["resolved"], 4)
            self.assertEqual(s["completed"], 3)


class StartGuardsTests(unittest.TestCase):
    def test_refuses_without_vm(self):
        ok, checks = start_guards("", 8, 10, 10, False, False)
        self.assertFalse(ok)
        self.assertTrue(any("DG_VM" in c for c in checks))

    def test_refuses_bad_workers(self):
        for bad in (0, 17, "many", None):
            ok, _ = start_guards("u@h", bad, 10, 10, False, False)
            self.assertFalse(ok, bad)

    def test_refuses_db_ahead_of_local(self):
        ok, checks = start_guards("u@h", 8, 10, 12, False, False)
        self.assertFalse(ok)
        self.assertTrue(any("reconcile" in c for c in checks))

    def test_refuses_unverifiable_drift(self):
        ok, _ = start_guards("u@h", 8, 10, None, False, False)
        self.assertFalse(ok)

    def test_refuses_double_start(self):
        ok, _ = start_guards("u@h", 8, 10, 10, False, True)
        self.assertFalse(ok)
        ok, _ = start_guards("u@h", 8, 10, 10, True, False)
        self.assertFalse(ok)

    def test_passes_clean_and_warns_high_workers(self):
        ok, checks = start_guards("u@h", 8, 10, 10, False, False)
        self.assertTrue(ok)
        ok, checks = start_guards("u@h", 16, 10, 10, False, False)
        self.assertTrue(ok)
        self.assertTrue(any("block_storm" in c for c in checks))


if __name__ == "__main__":
    unittest.main()
