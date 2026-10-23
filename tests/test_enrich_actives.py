"""`python3 -m tgpc.enrich_actives` — the phase-3 upsert of reactivated records.

This module writes to the production table, so the tests here are about what it
refuses to write: a record whose scraped identity disagrees with the sweep's
basic info must never be upserted, and two records that both have a NULL
serial_number must not collapse onto one identity.

Everything external is patched — Manager, Scraper, the Supabase client, the
progress bar and `time.time` (so the elapsed/rate line is deterministic).
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import tgpc.enrich_actives as enrich_actives


def _record(**kw):
    from tgpc.scraper import PharmacistRecord

    base = dict(
        registration_number="R1",
        name="N1",
        father_name="F1",
        category="BPharm",
        status="Active",
        gender="Female",
        validity_date="31-Dec-2026",
    )
    base.update(kw)
    return PharmacistRecord(**base)


class EnrichActivesTests(unittest.TestCase):
    def _run(self, lines, detail_for, argv=(), photo_ok=True, account_id=None):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        active_file = tmp / "actives.jsonl"
        active_file.write_text("".join(json.dumps(ln) + "\n" for ln in lines), encoding="utf-8")

        supabase = MagicMock()
        mgr = MagicMock()
        mgr.config.enrichment_directory = str(tmp)
        mgr.config.r2_public_base = "https://photos.example"
        mgr.upload_and_verify_photo = MagicMock(return_value=photo_ok)

        scraped = []

        def factory():
            class FakeScraper:
                def __init__(self):
                    from types import SimpleNamespace

                    self.rate_limiter = SimpleNamespace(min_delay=3.0, current_delay=3.0)

                def extract_detailed_info(self, reg, img_dir=None):
                    scraped.append(reg)
                    return detail_for(reg)

            return FakeScraper()

        bar = MagicMock()
        bar.__enter__ = MagicMock(return_value=bar)
        bar.__exit__ = MagicMock(return_value=False)

        env = {"CLOUDFLARE_ACCOUNT_ID": account_id} if account_id else {}
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["tgpc.enrich_actives", *argv]),
            patch.object(enrich_actives, "ACTIVE_FILE", active_file),
            patch.object(enrich_actives, "_sb_client", return_value=supabase),
            patch("tgpc.manager.Manager", return_value=mgr),
            patch("tgpc.scraper.Scraper", factory),
            patch("tgpc.progress.ProgressBar", return_value=bar),
            patch("tgpc.progress.step"),
            patch.dict(enrich_actives.os.environ, env, clear=True),
            patch("tgpc.enrich_actives.time.time", side_effect=[100.0, 105.0]),
            contextlib.redirect_stdout(out),
        ):
            enrich_actives.main()

        payloads = [c.args[0] for c in supabase.table.return_value.upsert.call_args_list]
        return payloads, scraped, out.getvalue(), mgr

    def test_matching_record_is_upserted_with_basic_identity(self):
        lines = [
            {"registration_number": "R1", "name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 7}
        ]
        payloads, _, out, _ = self._run(lines, lambda reg: _record(registration_number=reg))

        self.assertEqual(len(payloads), 1)
        payload = payloads[0]
        self.assertEqual(payload["registration_number"], "R1")
        self.assertEqual(payload["name"], "N1", "identity comes from the sweep's basic info")
        self.assertEqual(payload["serial_number"], 7)
        self.assertEqual(payload["status"], "Active")
        self.assertIn("Stats: {'processed': 1, 'failed': 0, 'no_photo': 1}", out)

    def test_identity_mismatch_is_refused(self):
        lines = [
            {"registration_number": "R1", "name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 7}
        ]
        payloads, _, out, _ = self._run(lines, lambda reg: _record(registration_number=reg, name="Someone Else"))

        self.assertEqual(payloads, [], "a mismatched record must never reach the table")
        self.assertIn("'processed': 0, 'failed': 1", out)

    def test_null_serial_numbers_do_not_collide(self):
        lines = [
            {
                "registration_number": "R1",
                "name": "N1",
                "father_name": "F1",
                "category": "BPharm",
                "serial_number": None,
            },
            {
                "registration_number": "R2",
                "name": "N2",
                "father_name": "F2",
                "category": "DPharm",
                "serial_number": None,
            },
        ]
        payloads, _, _, _ = self._run(
            lines,
            lambda reg: _record(
                registration_number=reg,
                name={"R1": "N1", "R2": "N2"}[reg],
                father_name={"R1": "F1", "R2": "F2"}[reg],
                category={"R1": "BPharm", "R2": "DPharm"}[reg],
            ),
        )

        self.assertEqual(sorted(p["registration_number"] for p in payloads), ["R1", "R2"])
        self.assertEqual({p["registration_number"]: p["name"] for p in payloads}, {"R1": "N1", "R2": "N2"})

    def test_verified_photo_is_uploaded_linked_and_deleted_locally(self):
        lines = [
            {"registration_number": "R1", "name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 1}
        ]
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # The module writes photos under <enrichment_directory>/webp.
        (tmp / "webp").mkdir()
        (tmp / "webp" / "R1.webp").write_bytes(b"RIFF-fake-webp")

        supabase = MagicMock()
        mgr = MagicMock()
        mgr.config.enrichment_directory = str(tmp)
        mgr.config.r2_public_base = "https://photos.example"
        mgr.upload_and_verify_photo = MagicMock(return_value=True)

        with (
            patch.object(sys, "argv", ["tgpc.enrich_actives"]),
            patch.object(
                enrich_actives,
                "ACTIVE_FILE",
                self._write(tmp / "actives.jsonl", lines),
            ),
            patch.object(enrich_actives, "_sb_client", return_value=supabase),
            patch("tgpc.manager.Manager", return_value=mgr),
            patch("tgpc.scraper.Scraper", self._scraper(lambda reg: _record(registration_number=reg))),
            patch("tgpc.progress.ProgressBar", return_value=MagicMock()),
            patch("tgpc.progress.step"),
            patch.dict(enrich_actives.os.environ, {"CLOUDFLARE_ACCOUNT_ID": "acct"}, clear=True),
            patch("tgpc.enrich_actives.time.time", side_effect=[100.0, 105.0]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            enrich_actives.main()

        mgr.upload_and_verify_photo.assert_called_once()
        payload = supabase.table.return_value.upsert.call_args.args[0]
        self.assertEqual(payload["photo_url"], "https://photos.example/photos/R1.webp")
        self.assertFalse((tmp / "webp" / "R1.webp").exists(), "verified photos are removed locally")

    def test_failed_photo_is_kept_and_reported(self):
        lines = [
            {"registration_number": "R1", "name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 1}
        ]
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "webp").mkdir()
        photo = tmp / "webp" / "R1.webp"
        photo.write_bytes(b"RIFF-fake-webp")

        supabase = MagicMock()
        mgr = MagicMock()
        mgr.config.enrichment_directory = str(tmp)
        mgr.config.r2_public_base = "https://photos.example"
        mgr.upload_and_verify_photo = MagicMock(return_value=False)

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["tgpc.enrich_actives"]),
            patch.object(enrich_actives, "ACTIVE_FILE", self._write(tmp / "actives.jsonl", lines)),
            patch.object(enrich_actives, "_sb_client", return_value=supabase),
            patch("tgpc.manager.Manager", return_value=mgr),
            patch("tgpc.scraper.Scraper", self._scraper(lambda reg: _record(registration_number=reg))),
            patch("tgpc.progress.ProgressBar", return_value=MagicMock()),
            patch("tgpc.progress.step"),
            patch.dict(enrich_actives.os.environ, {}, clear=True),
            patch("tgpc.enrich_actives.time.time", side_effect=[100.0, 105.0]),
            contextlib.redirect_stdout(out),
        ):
            enrich_actives.main()

        self.assertTrue(photo.exists(), "a failed upload must not delete the only copy")
        payload = supabase.table.return_value.upsert.call_args.args[0]
        self.assertFalse(payload.get("photo_url"), "photo_url is set only after a verified upload")
        self.assertIn("PHOTO FAILED (1): R1", out.getvalue())

    def test_limit_and_worker_count_process_every_record_once(self):
        lines = [
            {
                "registration_number": f"R{i}",
                "name": f"N{i}",
                "father_name": "F",
                "category": "BPharm",
                "serial_number": i,
            }
            for i in (1, 2, 3)
        ]
        by_reg = {ln["registration_number"]: ln for ln in lines}

        def detail_for(reg):
            ln = by_reg[reg]
            return _record(registration_number=reg, name=ln["name"], father_name=ln["father_name"])

        payloads, scraped, _, _ = self._run(lines, detail_for, argv=["--workers", "2"])
        self.assertEqual(sorted(scraped), ["R1", "R2", "R3"])
        self.assertEqual(len(payloads), 3)

        payloads, scraped, _, _ = self._run(lines, detail_for, argv=["--limit", "1"])
        self.assertEqual(scraped, ["R1"])
        self.assertEqual(len(payloads), 1)

    def test_single_worker_path_and_missing_record(self):
        lines = [
            {"registration_number": "R1", "name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 1},
            {"registration_number": "R2", "name": "N2", "father_name": "F2", "category": "BPharm", "serial_number": 2},
        ]
        payloads, scraped, out, _ = self._run(
            lines,
            lambda reg: _record(registration_number=reg, name="N1", father_name="F1") if reg == "R1" else None,
            argv=["--workers", "1"],
        )

        self.assertEqual(scraped, ["R1", "R2"], "workers=1 walks the list in order")
        self.assertEqual([p["registration_number"] for p in payloads], ["R1"])
        self.assertIn("'processed': 1, 'failed': 1", out, "a record gone from the source counts as failed")

    def test_registration_number_mismatch_is_refused(self):
        lines = [
            {"registration_number": "R1", "name": "N1", "father_name": "F1", "category": "BPharm", "serial_number": 1}
        ]
        payloads, _, out, _ = self._run(lines, lambda reg: _record(registration_number="R2"))

        self.assertEqual(payloads, [], "the scraped record belongs to someone else")
        self.assertIn("'processed': 0, 'failed': 1", out)

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _write(path: Path, lines):
        path.write_text("".join(json.dumps(ln) + "\n" for ln in lines), encoding="utf-8")
        return path

    @staticmethod
    def _scraper(detail_for):
        def factory():
            class FakeScraper:
                def __init__(self):
                    from types import SimpleNamespace

                    self.rate_limiter = SimpleNamespace(min_delay=3.0, current_delay=3.0)

                def extract_detailed_info(self, reg, img_dir=None):
                    return detail_for(reg)

            return FakeScraper()

        return factory


if __name__ == "__main__":
    unittest.main()
