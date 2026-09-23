import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# tgpc/__init__ pulls Manager -> supabase; mock before import (cf test_scraper.py)
sys.modules.setdefault("supabase", MagicMock())

from tgpc.details_dg import (  # noqa: E402
    append_jsonl,
    build_supabase_payload,
    identity_matches,
    load_checkpoint,
    normalize_gender,
    parse_dg_html,
    run_fetch,
    save_checkpoint_atomic,
    valid_email,
    valid_mobile,
    validate_parsed,
    DgDetailError,
)

DG_HTML = """
<html><body>
<div>Pharmacist Details</div>
<table>
<tr><td></td></tr>
<tr><th>Registration No</th><td>TS003261</td><th>Gender</th><td>m</td></tr>
<tr><th>Pharmacist Name</th><td>SALLA DINESH REDDY</td><th>Father Name</th><td>SALLA BAL REDDY</td></tr>
<tr><th>Date Of Birth</th><td>10-11-1994</td><th>Category</th><td>BPharm</td></tr>
<tr><th>Date Of Registration</th><td>24-04-2019</td><th>Renewal Validity</th><td>31-12-2029</td></tr>
<tr><th>Address</th><td>5-8-744/2, Ashok Nagar</td><th>State</th><td>TELANGANA</td></tr>
<tr><th>Working/Studying Address</th><td></td><th>Working/Studying State</th><td></td></tr>
<tr><th>Mobile No</th><td>9550725290</td><th>Email Id</th><td>dinesh.govx@gmail.com</td></tr>
</table>
</body></html>
"""

CAPTCHA_FAIL_HTML = """
<html><body>
<div>Get Pharmacist Details</div>
<form><input type="text" name="registration_no"/>
<input type="text" name="recapcha"/>
</form>
</body></html>
"""


class ParseTests(unittest.TestCase):
    def test_parse_ok(self):
        parsed, raw = parse_dg_html(DG_HTML, "TS003261")
        self.assertEqual(parsed["registration_number"], "TS003261")
        self.assertEqual(parsed["name"], "SALLA DINESH REDDY")
        self.assertEqual(parsed["gender"], "Male")
        self.assertEqual(parsed["dob"], "10-11-1994")
        self.assertEqual(parsed["mobile_no"], "9550725290")
        self.assertEqual(parsed["email_id"], "dinesh.govx@gmail.com")
        self.assertEqual(validate_parsed(parsed), [])

    def test_parse_case_insensitive_reg(self):
        parsed, _ = parse_dg_html(DG_HTML, "ts003261")
        self.assertEqual(parsed["registration_number"], "TS003261")

    def test_echo_mismatch(self):
        with self.assertRaises(DgDetailError):
            parse_dg_html(DG_HTML, "TS003260")

    def test_captcha_fail_detected(self):
        with self.assertRaises(DgDetailError):
            parse_dg_html(CAPTCHA_FAIL_HTML, "TS003261")

    def test_not_found_detected(self):
        with self.assertRaises(DgDetailError):
            parse_dg_html("<html><body>No Records Found</body></html>", "TS003261")

    def test_not_authorized_is_terminal(self):
        html = "<html><body><table><tr><td>You are not Authorized to view the content</td></tr></table></body></html>"
        with self.assertRaises(DgDetailError) as ctx:
            parse_dg_html(html, "TS000002")
        self.assertTrue(ctx.exception.terminal)

    def test_form_page_with_captcha_word_is_not_a_block(self):
        # Regression: scraper BLOCKED_MARKERS contains "captcha", but the DG
        # form and captcha-fail pages legitimately mention captchas.
        from tgpc.details_dg import _is_blocked_dg

        self.assertFalse(_is_blocked_dg(CAPTCHA_FAIL_HTML))
        self.assertTrue(_is_blocked_dg("<html><body>Access Denied</body></html>"))


class ValidatorTests(unittest.TestCase):
    def test_mobile(self):
        self.assertTrue(valid_mobile("9550725290"))
        self.assertFalse(valid_mobile("12345"))
        self.assertFalse(valid_mobile("5050725290"))

    def test_email(self):
        self.assertTrue(valid_email("Dinesh.Govx@Gmail.Com "))
        self.assertFalse(valid_email("not-an-email"))

    def test_bad_fields_listed(self):
        parsed, _ = parse_dg_html(DG_HTML, "TS003261")
        parsed["mobile_no"] = "123"
        parsed["email_id"] = "bad"
        problems = validate_parsed(parsed)
        self.assertIn("bad_mobile", problems)
        self.assertIn("bad_email", problems)

    def test_gender_map(self):
        self.assertEqual(normalize_gender("m"), "Male")
        self.assertEqual(normalize_gender("F"), "Female")

    def test_identity_guard(self):
        parsed, _ = parse_dg_html(DG_HTML, "TS003261")
        ref = {"name": "salla dinesh reddy", "father_name": "SALLA BAL REDDY", "category": "BPharm"}
        self.assertTrue(identity_matches(parsed, ref))
        self.assertTrue(identity_matches(parsed, None))
        bad = dict(ref, name="Someone Else")
        self.assertFalse(identity_matches(parsed, bad))

    def test_reg_prefixes(self):
        from tgpc.details_dg import REG_RE

        for reg in ("TS123", "TG456", "TSDR789", "TGDR10", "ts000001"):
            self.assertRegex(reg, REG_RE)
        for reg in ("TS", "XX123", "TS12A", "", "RPH123"):
            self.assertNotRegex(reg, REG_RE)

    def test_guard_truncation_and_correction(self):
        from tgpc.details_dg import _match_detail

        base = {"name": "N", "father_name": "DEVARAKONDA LAKSHMI NARAYANA", "category": "BPharm"}
        # Bulk-table truncation of father name -> accept with note
        ok, notes = _match_detail(base, {"name": "n", "father_name": "Devarakonda Lakshmi Naray", "category": "BPharm"})
        self.assertTrue(ok)
        self.assertTrue(any(n.startswith("father_truncated:") for n in notes))
        # Upstream category correction -> accept with note
        ok, notes = _match_detail(base, {"name": "N", "father_name": base["father_name"], "category": "DPharm"})
        self.assertTrue(ok)
        self.assertTrue(any(n.startswith("category_changed:") for n in notes))
        # Name mismatch -> reject even if the rest matches
        ok, _ = _match_detail(base, {"name": "Other", "father_name": base["father_name"], "category": "BPharm"})
        self.assertFalse(ok)
        # Both father and category mismatch -> reject
        ok, _ = _match_detail(base, {"name": "N", "father_name": "Someone Else", "category": "DPharm"})
        self.assertFalse(ok)


class CheckpointTests(unittest.TestCase):
    def test_roundtrip_and_resume_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "cp.json"
            self.assertEqual(
                load_checkpoint(cp),
                {"completed": [], "failed": {}, "failed_terminal": {}},
            )
            save_checkpoint_atomic(cp, {"completed": ["TS1"], "failed": {}})
            self.assertEqual(load_checkpoint(cp)["completed"], ["TS1"])

            out = Path(tmp) / "out.jsonl"
            raw = Path(tmp) / "raw"
            stats = Path(tmp) / "stats.json"

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

            # TS1 already completed -> skipped without fetching
            calls = []
            orig = FakeFetcher.fetch_one

            def counting(self, reg, captcha_solver=None, max_captcha_attempts=3):
                calls.append(reg)
                html = DG_HTML.replace("TS003261", reg)
                html = html.replace("SALLA DINESH REDDY", "x").replace("SALLA BAL REDDY", "y")
                html = html.replace(">BPharm<", ">BPharm<")
                return html, {"captcha_text": "X", "attempts": 1, "ms": {}}

            FakeFetcher.fetch_one = counting
            try:
                stats_d = run_fetch(
                    ["TS1", "TS2"],
                    out,
                    raw,
                    cp,
                    stats,
                    reference={"TS2": {"name": "x", "father_name": "y", "category": "BPharm"}},
                    resume=True,
                    fetcher_factory=FakeFetcher,
                )
            finally:
                FakeFetcher.fetch_one = orig
            self.assertEqual(calls, ["TS2"])  # TS1 skipped via checkpoint
            self.assertEqual(stats_d["done"], 1)
            self.assertTrue((raw / "TS2.json").exists())
            rows = [json.loads(ln) for ln in out.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            append_jsonl(out, {"a": 1})
            self.assertEqual(len(out.read_text().splitlines()), 2)

    def test_terminal_skipped_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "cp.json"
            out = Path(tmp) / "out.jsonl"
            raw = Path(tmp) / "raw"
            stats = Path(tmp) / "stats.json"
            save_checkpoint_atomic(cp, {"completed": [], "failed": {"TS9": "x"}, "failed_terminal": {"TS9": "x"}})

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    raise AssertionError("terminal reg must not be fetched")

            stats_d = run_fetch(["TS9"], out, raw, cp, stats, resume=True, fetcher_factory=FakeFetcher)
            self.assertEqual(stats_d["already_done"], 1)
            self.assertEqual(stats_d["done"], 0)

            stats_d = run_fetch(
                ["TS9"],
                out,
                raw,
                cp,
                stats,
                resume=True,
                retry_terminal=True,
                fetcher_factory=FakeFetcher,
            )
            # --retry-terminal re-attempts (fetcher raises -> caught as failure, not skip)
            self.assertEqual(stats_d["already_done"], 0)


class SyncPayloadTests(unittest.TestCase):
    def test_payload_mapping(self):
        from tgpc.details_dg import DG_TABLE

        self.assertEqual(DG_TABLE, "rph_dg_contacts")  # separate table, never rph
        parsed, _ = parse_dg_html(DG_HTML, "TS003261")
        payload = build_supabase_payload(parsed, "2026-09-17T00:00:00Z", serial=3348)
        self.assertEqual(payload["registration_number"], "TS003261")
        self.assertEqual(payload["serial_number"], 3348)
        self.assertEqual(payload["mobile_no"], "9550725290")
        self.assertEqual(payload["email_id"], "dinesh.govx@gmail.com")
        self.assertEqual(payload["dob"], "10-11-1994")
        self.assertEqual(payload["home_state"], "TELANGANA")
        self.assertEqual(payload["work_study_address"], "")
        self.assertEqual(payload["dg_fetched_at"], "2026-09-17T00:00:00Z")

    def test_sync_cloud_batches_upsert(self):
        import tgpc.details_dg as dg

        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "cp.json"
            out = Path(tmp) / "out.jsonl"
            raw = Path(tmp) / "raw"
            stats = Path(tmp) / "stats.json"

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    html = DG_HTML.replace("TS003261", reg)
                    return html, {"captcha_text": "X", "attempts": 1, "ms": {}}

            calls = []
            orig = dg.upsert_dg_batch
            orig_snap = dg.sync_cloud_snapshot
            orig_r2 = dg.push_file_to_r2

            def fake_upsert(payloads):
                calls.append([p["registration_number"] for p in payloads])
                return len(payloads), ""

            r2_calls = []
            dg.upsert_dg_batch = fake_upsert
            dg.sync_cloud_snapshot = lambda *a: {}
            dg.push_file_to_r2 = lambda *a: (r2_calls.append(True), True)[1]  # hermetic
            ref = {
                r: {"name": "SALLA DINESH REDDY", "father_name": "SALLA BAL REDDY", "category": "BPharm"}
                for r in ("TS21", "TS22", "TS23")
            }
            try:
                stats_d = run_fetch(
                    ["TS21", "TS22", "TS23"],
                    out,
                    raw,
                    cp,
                    stats,
                    reference=ref,
                    resume=False,
                    sync_cloud=True,
                    sync_every=2,
                    fetcher_factory=FakeFetcher,
                )
            finally:
                dg.upsert_dg_batch = orig
                dg.sync_cloud_snapshot = orig_snap
                dg.push_file_to_r2 = orig_r2
            self.assertEqual(stats_d["done"], 3)
            self.assertEqual(stats_d["sb_upserted"], 3)
            self.assertEqual([len(c) for c in calls], [2, 1])  # batch of 2 + final flush
            self.assertEqual(stats_d["cloud_snapshot"], {})
            self.assertTrue(r2_calls)  # batch R2 seam exercised under mock (never real)

    def test_sync_cloud_requires_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_fetch(
                    ["TS1"],
                    Path(tmp) / "o.jsonl",
                    Path(tmp) / "r",
                    Path(tmp) / "c.json",
                    Path(tmp) / "s.json",
                    reference=None,
                    resume=False,
                    sync_cloud=True,
                    fetcher_factory=lambda: None,
                )

    def test_failed_batch_retried_at_end(self):
        import tgpc.details_dg as dg

        with tempfile.TemporaryDirectory() as tmp:
            cp, out, raw, stats = (Path(tmp) / n for n in ("c.json", "o.jsonl", "r", "s.json"))

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

            attempts = []
            orig = dg.upsert_dg_batch
            orig_snap = dg.sync_cloud_snapshot
            orig_r2 = dg.push_file_to_r2

            def flaky(payloads):
                attempts.append(len(payloads))
                if len(attempts) == 1:
                    return 0, "boom"
                return len(payloads), ""

            dg.upsert_dg_batch = flaky
            dg.sync_cloud_snapshot = lambda *a: {}
            dg.push_file_to_r2 = lambda *a: True  # hermetic: no real cloud writes in tests
            ref = {"TS31": {"name": "SALLA DINESH REDDY", "father_name": "SALLA BAL REDDY", "category": "BPharm"}}
            try:
                stats_d = run_fetch(
                    ["TS31"],
                    out,
                    raw,
                    cp,
                    stats,
                    reference=ref,
                    resume=False,
                    sync_cloud=True,
                    sync_every=50,
                    fetcher_factory=FakeFetcher,
                )
            finally:
                dg.upsert_dg_batch = orig
                dg.sync_cloud_snapshot = orig_snap
                dg.push_file_to_r2 = orig_r2
            self.assertEqual(stats_d["done"], 1)
            self.assertEqual(stats_d["sb_upserted"], 1)  # deferred batch healed at end
            self.assertEqual(stats_d["sb_batches_failed"], 1)
            self.assertNotIn("sb_pending", stats_d)

    def test_serial_attached_from_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, raw, cp, stats = (
                Path(tmp) / "o.jsonl",
                Path(tmp) / "r",
                Path(tmp) / "c.json",
                Path(tmp) / "s.json",
            )

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    html = DG_HTML.replace("TS003261", reg)
                    html = html.replace("SALLA DINESH REDDY", "N").replace("SALLA BAL REDDY", "F")
                    return html, {"captcha_text": "X", "attempts": 1, "ms": {}}

            ref = {"TS901": {"name": "N", "father_name": "F", "category": "BPharm", "serial_number": 901}}
            stats_d = run_fetch(
                ["TS901"], out, raw, cp, stats, reference=ref, resume=False, fetcher_factory=FakeFetcher
            )
            self.assertEqual(stats_d["done"], 1)
            row = json.loads(out.read_text().splitlines()[0])
            self.assertEqual(row["serial_number"], 901)
            raw_rec = json.loads((Path(tmp) / "r" / "TS901.json").read_text())
            self.assertEqual(raw_rec["serial_number"], 901)
            hist = json.loads((Path(tmp) / "dg_history.jsonl").read_text().splitlines()[0])
            self.assertEqual(hist["serial_number"], 901)

    def test_unknown_reg_saved_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, out, raw, stats = (Path(tmp) / n for n in ("c.json", "o.jsonl", "r", "s.json"))

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

            stats_d = run_fetch(
                ["TS99"],
                out,
                raw,
                cp,
                stats,
                reference={"OTHER": {"name": "x", "father_name": "y", "category": "BPharm"}},
                resume=False,
                fetcher_factory=FakeFetcher,
            )
            self.assertEqual(stats_d["done"], 1)
            self.assertNotIn("quarantined", stats_d)  # stat removed with quarantine plumbing
            row = json.loads(out.read_text().splitlines()[0])
            self.assertIn("unknown_reg", row["raw_notes"])
            # Fast-capture never quarantines; unknown regs are saved as-is

    def test_bad_fields_saved_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, out, raw, stats = (Path(tmp) / n for n in ("c.json", "o.jsonl", "r", "s.json"))

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    html = DG_HTML.replace("TS003261", reg)
                    html = html.replace("9550725290", "123").replace("dinesh.govx@gmail.com", "bad")
                    return html, {"captcha_text": "X", "attempts": 1, "ms": {}}

            ref = {"TS77": {"name": "SOMEONE ELSE", "father_name": "OTHER FATHER", "category": "BPharm"}}
            stats_d = run_fetch(
                ["TS77"],
                out,
                raw,
                cp,
                stats,
                reference=ref,
                resume=False,
                fetcher_factory=FakeFetcher,
            )
            self.assertEqual(stats_d["done"], 1)
            self.assertNotIn("quarantined", stats_d)  # stat removed with quarantine plumbing
            row = json.loads(out.read_text().splitlines()[0])
            self.assertEqual(row["mobile_no"], "123")
            self.assertEqual(row["email_id"], "bad")
            self.assertIn("bad_mobile", row["raw_notes"])
            self.assertIn("bad_email", row["raw_notes"])
            state = load_checkpoint(cp)
            self.assertIn("TS77", state["completed"])
            self.assertNotIn("quarantined", state)  # quarantine tracking removed

    def test_heal_jsonl_truncates_torn_line(self):
        from tgpc.details_dg import _heal_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.jsonl"
            p.write_text('{"a": 1}\n{"b": 2}\n{"torn": ', encoding="utf-8")
            self.assertEqual(_heal_jsonl(p), 1)
            self.assertEqual(p.read_text().splitlines(), ['{"a": 1}', '{"b": 2}'])
            self.assertEqual(_heal_jsonl(p), 0)  # healthy file untouched
            self.assertEqual(_heal_jsonl(Path(tmp) / "missing.jsonl"), 0)


class WorkerFixedTests(unittest.TestCase):
    def test_fetch_pool_is_fixed_at_four(self):
        import tgpc.details_dg as dg
        from tgpc.details_dg import DG_WORKERS

        self.assertEqual(DG_WORKERS, 4)
        created = []
        orig = dg.ThreadPoolExecutor

        def recording(*args, **kwargs):
            created.append(kwargs.get("max_workers"))
            return orig(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            cp, out, raw, stats = (Path(tmp) / n for n in ("c.json", "o.jsonl", "r", "s.json"))

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

            regs = [f"TS8{i:02d}" for i in range(6)]
            dg.ThreadPoolExecutor = recording
            try:
                stats_d = run_fetch(regs, out, raw, cp, stats, resume=False, fetcher_factory=FakeFetcher)
            finally:
                dg.ThreadPoolExecutor = orig
            self.assertEqual(created, [4])
            self.assertEqual(stats_d["done"], len(regs))
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(sorted(r["registration_number"] for r in rows), sorted(regs))


class LiveWatchTests(unittest.TestCase):
    def _paths(self, tmp):
        d = Path(tmp)
        return d / "o.jsonl", d / "r", d / "c.json", d / "s.json"

    def _fetcher(self, trigger=None):
        class FakeFetcher:
            def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                if trigger:
                    trigger(reg)
                return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

        return FakeFetcher

    def test_max_records_bounds_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, raw, cp, stats = self._paths(tmp)
            stats_d = run_fetch(
                ["TS701", "TS702", "TS703"],
                out,
                raw,
                cp,
                stats,
                resume=False,
                max_records=2,
                fetcher_factory=self._fetcher(),
            )
            self.assertEqual(stats_d["done"], 2)
            live = json.loads((Path(tmp) / "dg_live.json").read_text())
            self.assertEqual(live["status"], "finished")
            self.assertEqual(live["done"], 2)
            self.assertTrue((Path(tmp) / "dg_fetch.log").exists())
            hist = [json.loads(ln) for ln in (Path(tmp) / "dg_history.jsonl").read_text().splitlines()]
            self.assertEqual(len(hist), 2)
            self.assertTrue(
                all(h["outcome"] == "saved" and h["registration_number"] in ("TS701", "TS702") for h in hist)
            )
            self.assertTrue(all("serial_number" in h for h in hist))  # tracker on every line
            live = json.loads((Path(tmp) / "dg_live.json").read_text())
            self.assertIn("serial_number", live)  # live view carries tracker too

    def test_stop_file_halts_after_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, raw, cp, stats = self._paths(tmp)
            seen = []

            def trigger(reg):
                seen.append(reg)
                if len(seen) == 1:
                    (Path(tmp) / "dg_stop").write_text("", encoding="utf-8")

            stats_d = run_fetch(
                ["TS801", "TS802", "TS803"],
                out,
                raw,
                cp,
                stats,
                resume=False,
                fetcher_factory=self._fetcher(trigger),
            )
            self.assertTrue(stats_d.get("stopped"))
            self.assertEqual(stats_d["done"], 1)
            self.assertFalse((Path(tmp) / "dg_stop").exists())  # consumed
            live = json.loads((Path(tmp) / "dg_live.json").read_text())
            self.assertEqual(live["status"], "stopped")
            # Resume continues where it stopped
            stats_d2 = run_fetch(
                ["TS801", "TS802", "TS803"],
                out,
                raw,
                cp,
                stats,
                resume=True,
                fetcher_factory=self._fetcher(),
            )
            self.assertEqual(stats_d2["done"], 2)
            self.assertNotIn("stopped", stats_d2)


class WarpGateTests(unittest.TestCase):
    def _paths(self, tmp):
        d = Path(tmp)
        return d / "o.jsonl", d / "r", d / "c.json", d / "s.json"

    def _fetcher(self):
        class FakeFetcher:
            def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=1):
                return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

        return FakeFetcher

    def test_ensure_rotated_success_on_retry(self):
        import tgpc.details_dg as dg

        orig_cycle, orig_egress = dg.cycle_warp, dg.egress_ip
        seen = {"n": 0}

        def fake_cycle():
            seen["n"] += 1
            return True

        dg.cycle_warp = fake_cycle
        dg.egress_ip = lambda timeout=15: "B" if seen["n"] >= 2 else "A"
        try:
            ip, rotated = dg.ensure_rotated_ip("A", max_cycles=3)
        finally:
            dg.cycle_warp, dg.egress_ip = orig_cycle, orig_egress
        self.assertTrue(rotated)
        self.assertEqual(ip, "B")

    def test_ensure_rotated_gives_up(self):
        import tgpc.details_dg as dg

        orig_cycle, orig_egress = dg.cycle_warp, dg.egress_ip
        dg.cycle_warp = lambda: True
        dg.egress_ip = lambda timeout=15: "A"
        try:
            ip, rotated = dg.ensure_rotated_ip("A", max_cycles=2)
        finally:
            dg.cycle_warp, dg.egress_ip = orig_cycle, orig_egress
        self.assertFalse(rotated)
        self.assertEqual(ip, "A")

    def test_gate_halts_when_unrotated(self):
        import tgpc.details_dg as dg

        with tempfile.TemporaryDirectory() as tmp:
            out, raw, cp, stats = self._paths(tmp)
            orig_avail, orig_egress, orig_ensure = dg.warp_available, dg.egress_ip, dg.ensure_rotated_ip
            dg.warp_available = lambda: True
            dg.egress_ip = lambda timeout=15: "A"
            dg.ensure_rotated_ip = lambda prev, max_cycles=3: ("A", False)
            try:
                stats_d = run_fetch(
                    ["TS711", "TS712", "TS713"],
                    out,
                    raw,
                    cp,
                    stats,
                    resume=False,
                    warp_rotate_every=2,
                    fetcher_factory=self._fetcher(),
                )
            finally:
                dg.warp_available, dg.egress_ip = orig_avail, orig_egress
                dg.ensure_rotated_ip = orig_ensure
            self.assertTrue(stats_d.get("stopped"))
            self.assertEqual(stats_d.get("stop_reason"), "ip_rotation_failed")
            self.assertEqual(stats_d["done"], 2)  # gate fires before 3rd (cumulative 2)

    def test_gate_passes_when_rotated(self):
        import tgpc.details_dg as dg

        with tempfile.TemporaryDirectory() as tmp:
            out, raw, cp, stats = self._paths(tmp)
            orig_avail, orig_egress, orig_ensure = dg.warp_available, dg.egress_ip, dg.ensure_rotated_ip
            dg.warp_available = lambda: True
            dg.egress_ip = lambda timeout=15: "A"
            dg.ensure_rotated_ip = lambda prev, max_cycles=3: ("B", True)
            try:
                stats_d = run_fetch(
                    ["TS721", "TS722", "TS723"],
                    out,
                    raw,
                    cp,
                    stats,
                    resume=False,
                    warp_rotate_every=2,
                    fetcher_factory=self._fetcher(),
                )
            finally:
                dg.warp_available, dg.egress_ip = orig_avail, orig_egress
                dg.ensure_rotated_ip = orig_ensure
            self.assertEqual(stats_d["done"], 3)
            self.assertEqual(stats_d.get("warp_rotations"), 1)  # fired once before 3rd (cumulative 2)
            self.assertNotIn("stopped", stats_d)

    def test_gate_counts_across_resumed_runs(self):
        import tgpc.details_dg as dg

        with tempfile.TemporaryDirectory() as tmp:
            out, raw, cp, stats = (
                Path(tmp) / "o.jsonl",
                Path(tmp) / "r",
                Path(tmp) / "c.json",
                Path(tmp) / "s.json",
            )
            # Two regs done in a previous run; gate every=2 must fire before the
            # very first new record (cumulative already sits on a multiple).
            save_checkpoint_atomic(cp, {"completed": ["TS700", "TS701"], "failed": {}, "failed_terminal": {}})

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=1):
                    return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

            orig_avail, orig_egress, orig_ensure = dg.warp_available, dg.egress_ip, dg.ensure_rotated_ip
            dg.warp_available = lambda: True
            dg.egress_ip = lambda timeout=15: "A"
            dg.ensure_rotated_ip = lambda prev, max_cycles=3: ("B", True)
            try:
                stats_d = run_fetch(
                    ["TS700", "TS701", "TS702"],
                    out,
                    raw,
                    cp,
                    stats,
                    resume=True,
                    warp_rotate_every=2,
                    fetcher_factory=FakeFetcher,
                )
            finally:
                dg.warp_available, dg.egress_ip = orig_avail, orig_egress
                dg.ensure_rotated_ip = orig_ensure
            self.assertEqual(stats_d["done"], 1)
            self.assertEqual(stats_d.get("warp_rotations"), 1)

    def test_gate_refuses_without_warp(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, raw, cp, stats = self._paths(tmp)
            import tgpc.details_dg as dg

            orig_avail = dg.warp_available
            dg.warp_available = lambda: False
            try:
                with self.assertRaises(ValueError):
                    run_fetch(
                        ["TS731"],
                        out,
                        raw,
                        cp,
                        stats,
                        resume=False,
                        warp_rotate_every=5,
                        fetcher_factory=self._fetcher(),
                    )
            finally:
                dg.warp_available = orig_avail


class NoResumeGuardTests(unittest.TestCase):
    def test_no_resume_backs_up_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "c.json"
            out, raw, stats = (Path(tmp) / n for n in ("o.jsonl", "r", "s.json"))
            save_checkpoint_atomic(cp, {"completed": ["TS1"], "failed": {}, "failed_terminal": {}})

            class FakeFetcher:
                def fetch_one(self, reg, captcha_solver=None, max_captcha_attempts=3):
                    return DG_HTML.replace("TS003261", reg), {"captcha_text": "X", "attempts": 1, "ms": {}}

            run_fetch(["TS2"], out, raw, cp, stats, resume=False, fetcher_factory=FakeFetcher)
            baks = list(Path(tmp).glob("c.json.bak-*"))
            self.assertEqual(len(baks), 1)
            state = load_checkpoint(cp)
            self.assertIn("TS2", state["completed"])
            self.assertNotIn("TS1", state["completed"])  # fresh state, backup holds the old

    def test_cli_refuses_no_resume_without_allow_clobber(self):
        from unittest.mock import patch

        from tgpc import __main__ as cli

        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "c.json"
            cp.write_text(
                '{"completed": ["TS1"], "failed": {}, "failed_terminal": {}}',
                encoding="utf-8",
            )
            argv = ["tgpc", "fetch-dg", "--ids", "TS2", "--checkpoint", str(cp), "--no-resume"]
            with patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main()
            self.assertEqual(ctx.exception.code, 2)
            state = json.loads(cp.read_text(encoding="utf-8"))
            self.assertEqual(state["completed"], ["TS1"])  # untouched

    def test_cli_no_resume_proceeds_with_allow_clobber(self):
        from unittest.mock import patch

        from tgpc import __main__ as cli

        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "c.json"
            cp.write_text(
                '{"completed": ["TS1"], "failed": {}, "failed_terminal": {}}',
                encoding="utf-8",
            )
            argv = [
                "tgpc",
                "fetch-dg",
                "--ids",
                "TS2",
                "--checkpoint",
                str(cp),
                "--out",
                str(Path(tmp) / "o.jsonl"),
                "--raw-dir",
                str(Path(tmp) / "r"),
                "--stats",
                str(Path(tmp) / "s.json"),
                "--no-resume",
                "--allow-clobber",
            ]
            with patch.object(sys, "argv", argv):
                with patch("tgpc.details_dg.run_fetch", return_value={"done": 0}) as rf:
                    cli.main()
            self.assertTrue(rf.called)


class ValidateL1Tests(unittest.TestCase):
    def test_validate_l1_reports_bad_rows(self):
        from tgpc.details_dg import validate_l1

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o.jsonl"
            raw = Path(tmp) / "r"
            raw.mkdir()
            good = {
                "registration_number": "TS60",
                "name": "N",
                "gender": "Male",
                "mobile_no": "9550725290",
                "email_id": "a@b.com",
            }
            bad = {
                "registration_number": "TS61",
                "name": "N",
                "gender": "Male",
                "mobile_no": "123",
                "email_id": "bad",
            }
            out.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
            (raw / "TS60.json").write_text("{}", encoding="utf-8")
            stats = validate_l1(out, raw)
            self.assertEqual(stats["total"], 2)
            self.assertEqual(stats["clean"], 1)
            self.assertEqual(stats["flagged"], 1)
            self.assertEqual(stats["flagged_regs"], ["TS61"])
            self.assertIn("bad_mobile", stats["by_reason"])
            self.assertIn("bad_email", stats["by_reason"])
            self.assertEqual(stats["missing_raw_count"], 1)
            self.assertEqual(stats["missing_raw"], ["TS61"])


if __name__ == "__main__":
    unittest.main()
