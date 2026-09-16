"""Regression tests for the Sept-2026 bug sweep.

Each test pins one fixed bug: restore-to-temp, backup rotation baseline,
release return-code checks, --force overriding both safety guards,
DetailError vs genuine absence, WARP pre-existing connection, exact-count
save validation.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock supabase before importing manager
sys.modules["supabase"] = MagicMock()

from tgpc import __main__ as cli
from tgpc import quota
from tgpc.manager import Manager
from tgpc.scraper import DetailError, PharmacistRecord, Scraper
from tgpc.utils import Config


def record(reg_no, name="Name", father="Father", category="BPharm", serial=1):
    return PharmacistRecord(
        registration_number=reg_no,
        name=name,
        father_name=father,
        category=category,
        serial_number=serial,
    )


def make_manager(temp_dir: str, scraper=None) -> Manager:
    with patch("tgpc.manager.load_credentials"):
        with patch(
            "tgpc.manager.Config.load",
            return_value=Config(data_directory=temp_dir, enrichment_directory=temp_dir),
        ):
            with patch("tgpc.manager.Scraper", return_value=scraper or MagicMock()):
                return Manager()


def make_response(html: str):
    resp = MagicMock()
    resp.text = html
    resp.content = html.encode("utf-8")
    resp.status_code = 200
    return resp


class DetailErrorTests(unittest.TestCase):
    def test_unexpected_parse_failure_raises_not_none(self):
        scraper = Scraper()
        with patch.object(scraper, "_request", return_value=make_response("<html><body>No table</body></html>")):
            with patch("tgpc.scraper.BeautifulSoup", side_effect=RuntimeError("parse boom")):
                with self.assertRaises(DetailError):
                    scraper.extract_detailed_info("RPH999")

    def test_genuine_absence_still_returns_none(self):
        scraper = Scraper()
        with patch.object(
            scraper,
            "_request",
            return_value=make_response("<html><body>No Records Found</body></html>"),
        ):
            self.assertIsNone(scraper.extract_detailed_info("RPH404"))


class RestoreTests(unittest.TestCase):
    def test_failed_restore_leaves_local_file_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            # Empty local file fails validation; with no R2 endpoint the
            # restore must fail WITHOUT deleting the local file.
            manager.file_manager.save([])
            rph_path = Path(temp_dir) / "rph.json"
            with patch("tgpc.manager.os.environ", {"TGPC_ALLOW_SMALL_RPH": "1"}):
                self.assertFalse(manager._restore_rph_from_backup())
            self.assertTrue(rph_path.exists())

    def test_unvalidated_download_never_replaces_local(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([])
            before = (Path(temp_dir) / "rph.json").read_text()

            def fake_run(*args, **kwargs):
                cmd = args[0]
                if "get-object" in cmd:
                    # Simulate a truncated/HTML error page download (>100 bytes).
                    dest = Path(cmd[-1])
                    dest.write_text("<html>error</html>" * 20)
                    return MagicMock(returncode=0, stdout="", stderr="")
                if "list-objects-v2" in cmd:
                    payload = {
                        "Contents": [
                            {
                                "Key": "backups/rph_backup_20200101_000000.json",
                                "LastModified": "2020-01-01",
                                "Size": 5000,
                            }
                        ]
                    }
                    return MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
                raise AssertionError(f"unexpected aws call: {cmd}")

            env = {"CLOUDFLARE_ACCOUNT_ID": "x", "TGPC_ALLOW_SMALL_RPH": "1"}
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.subprocess.run", side_effect=fake_run):
                    self.assertFalse(manager._restore_rph_from_backup())
            self.assertEqual((Path(temp_dir) / "rph.json").read_text(), before)


class RotationBaselineTests(unittest.TestCase):
    def test_baseline_is_newest_not_oldest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            deleted = []

            def fake_run(*args, **kwargs):
                cmd = args[0]
                if "list-objects" in cmd:
                    # Oldest is HUGE, newest is tiny; candidate is 2x newest.
                    contents = [
                        {"Key": "backups/rph_backup_20200101_000000.json", "Size": 20_000_000},
                        *[
                            {"Key": f"backups/rph_backup_202101{i:02d}_000000.json", "Size": 1_000_000}
                            for i in range(1, 30)
                        ],
                        {"Key": "backups/rph_backup_20260101_000000.json", "Size": 1_000_000},  # newest
                        {"Key": "backups/rph_backup_20200301_000000.json", "Size": 2_000_000},  # candidate past #30
                    ]
                    return MagicMock(returncode=0, stdout=json.dumps({"Contents": contents}), stderr="")
                if "delete-object" in cmd:
                    deleted.append(cmd[cmd.index("--key") + 1])
                    return MagicMock(returncode=0, stdout="", stderr="")
                return MagicMock(returncode=0, stdout="", stderr="")

            env = {
                "CLOUDFLARE_ACCOUNT_ID": "x",
                "R2_ACCESS_KEY_ID": "a",
                "R2_SECRET_ACCESS_KEY": "b",
            }
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.subprocess.run", side_effect=fake_run):
                    self.assertTrue(manager.backup_manager._upload_to_r2(Path(temp_dir) / "rph.json"))
            # 2M > 1.5x newest (1M) so it must be KEPT. Old code used the
            # 20M oldest as baseline and would have deleted it.
            self.assertNotIn("backups/rph_backup_20200301_000000.json", deleted)


class ReleaseReturnCodeTests(unittest.TestCase):
    def test_upload_failure_returns_false(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            calls = {"n": 0}

            def fake_run(*args, **kwargs):
                cmd = args[0]
                calls["n"] += 1
                if "view" in cmd:
                    return MagicMock(returncode=0, stdout="", stderr="")
                if "upload" in cmd:
                    return MagicMock(returncode=1, stdout="", stderr="network down")
                return MagicMock(returncode=0, stdout="", stderr="")

            env = {
                "GITHUB_REPOSITORY": "org/repo",
                "RELEASE_PASSWORD": "pw",
                "TGPC_ALLOW_SMALL_RPH": "1",
            }
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.subprocess.run", side_effect=fake_run):
                    self.assertFalse(manager.sync_to_release())


class ForceGuardTests(unittest.TestCase):
    def test_force_overrides_ninety_percent_guard(self):
        os.environ["TGPC_ALLOW_SMALL_RPH"] = "1"
        self.addCleanup(os.environ.pop, "TGPC_ALLOW_SMALL_RPH", None)
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = [record(f"RPH{i:03d}", serial=i) for i in range(1, 101)]
            fresh = [record(f"RPH{i:03d}", serial=i) for i in range(1, 81)]  # 80% -> trips guard
            with patch("tgpc.manager.BackupManager._upload_to_r2", return_value=True):
                with patch("tgpc.manager.Manager._restore_rph_from_backup", return_value=True):
                    with patch("tgpc.manager.Config.load", return_value=Config(data_directory=temp_dir)):
                        scraper = MagicMock()
                        scraper.health_check.return_value = True
                        scraper.extract_basic_records.return_value = fresh
                        with patch("tgpc.manager.Scraper", return_value=scraper):
                            manager = Manager()
            manager.file_manager.save(existing)
            self.assertEqual(manager.run_daily_update(force=True), "updated")
            self.assertEqual(len(manager.file_manager.load()), 80)


class WarpTests(unittest.TestCase):
    def test_preexisting_connection_not_claimed(self):
        with patch(
            "tgpc.__main__.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="Status: Connected", stderr=""),
        ):
            # Already connected -> returns False so atexit leaves it up.
            self.assertFalse(cli._warp_connect())


class SaveValidationTests(unittest.TestCase):
    def test_truncated_roundtrip_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            with patch("tgpc.manager.json.load", return_value=[{"only": "one"}]):
                with self.assertRaises(ValueError):
                    manager.file_manager.save([record("R1", serial=1), record("R2", serial=2)])


class ScraperConfigTests(unittest.TestCase):
    def test_proxy_url_applied_to_session(self):
        with patch(
            "tgpc.scraper.Config.load",
            return_value=Config(proxy_url="http://proxy:8080"),
        ):
            scraper = Scraper()
        self.assertEqual(
            scraper.session.proxies.get("https"),
            "http://proxy:8080",
        )

    def test_no_proxy_leaves_session_alone(self):
        with patch("tgpc.scraper.Config.load", return_value=Config(proxy_url=None)):
            scraper = Scraper()
        self.assertNotIn("https", scraper.session.proxies)

    def test_user_agent_comes_from_config(self):
        with patch(
            "tgpc.scraper.Config.load",
            return_value=Config(user_agent="TestAgent/1.0"),
        ):
            scraper = Scraper()
        self.assertEqual(scraper.session.headers["User-Agent"], "TestAgent/1.0")


class QuotaEndpointTests(unittest.TestCase):
    def test_supabase_sql_uses_database_query_path(self):
        seen = []

        def fake_req_json(url, headers, data, timeout=15):
            seen.append(url)
            return {"status": 200, "body": '[{"size_gb": "0.1"}]', "headers": {}}

        env = {
            "SUPABASE_URL": "https://xyz999.supabase.co",
            "SUPABASE_PAT": "pat",
        }
        with patch("tgpc.quota.os.environ", env):
            with patch("tgpc.quota._req", return_value={"status": 404, "body": "", "headers": {}}):
                with patch("tgpc.quota._req_json", side_effect=fake_req_json):
                    quota.check_supabase()
        self.assertTrue(seen, "expected at least one SQL query")
        for url in seen:
            self.assertIn("/database/query", url)
            self.assertNotIn("/sql", url.replace("/database/query", ""))


class StandaloneEnrichTests(unittest.TestCase):
    def _details(self, reg_no):
        return PharmacistRecord(
            registration_number=reg_no,
            name="Name",
            father_name="Father",
            category="BPharm",
        )

    def test_standalone_enrich_checks_all_records(self):
        """Fresh Manager (no _last_new_regs) must not no-op."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001"), record("RPH002", serial=2)])
            manager.scraper = MagicMock()
            manager.scraper.extract_detailed_info.side_effect = lambda reg, img=None: self._details(reg)
            with patch("tgpc.manager.create_client", return_value=None):
                manager.enrich_new_records()
            calls = manager.scraper.extract_detailed_info.call_args_list
            regs = sorted(c.args[0] for c in calls)
            self.assertEqual(regs, ["RPH001", "RPH002"])

    def test_standalone_enrich_safety_abort_above_1000(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record(f"RPH{i:05d}", serial=i) for i in range(1, 1002)])
            manager.scraper = MagicMock()
            with patch("tgpc.manager.create_client", return_value=None):
                manager.enrich_new_records()
            manager.scraper.extract_detailed_info.assert_not_called()

    def test_update_path_zero_new_still_noops(self):
        """An update that found zero new records must keep no-op behavior."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            manager._last_new_regs = set()
            manager.scraper = MagicMock()
            with patch("tgpc.manager.create_client", return_value=None):
                manager.enrich_new_records()
            manager.scraper.extract_detailed_info.assert_not_called()


class EmailBrandColorTests(unittest.TestCase):
    DETAILS = {
        "new_details": ["RPH001 - Name (BPharm)"],
        "modified_details": [],
        "removed_details": [],
        "new_cat_stats": {"BPharm": 1},
        "rem_cat_stats": {},
        "mod_cat_stats": {},
        "total_records": 100,
    }

    def test_email_html_uses_only_brand_palette(self):
        import re

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager._last_update_details = dict(self.DETAILS)
            env = {"RESEND_API_KEY": "k", "NOTIFICATION_EMAIL": "to@example.com"}
            resp = MagicMock(ok=True, text='{"id":"abc"}')
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.requests.post", return_value=resp) as post:
                    self.assertTrue(manager.sync_to_email())
            html = post.call_args.kwargs["json"]["html"]
            allowed = {
                "00cc66",
                "ef4444",
                "9ca3af",
                "2563eb",
                "111827",
                "6b7280",
                "ffffff",
                "374151",
            }
            for m in re.finditer(r"#([0-9a-fA-F]{3,6})\b", html):
                self.assertIn(m.group(1).lower(), allowed, f"off-brand {m.group(0)} in email HTML")


class OptimizeImagesTests(unittest.TestCase):
    def test_main_noops_without_src_dir(self):
        import scripts.optimize_images as opt

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(opt, "SRC_DIR", Path(temp_dir) / "img"):
                with patch.object(opt, "DST_DIR", Path(temp_dir) / "webp"):
                    opt.main()  # must not raise
            self.assertFalse((Path(temp_dir) / "webp").exists())


if __name__ == "__main__":
    unittest.main()
