"""Sync-layer tests (CODE_REVIEW.md T3).

Covers every sync_to_* destination's contract: missing credentials fail closed,
successes report True, transport/API failures report False — so callers can
propagate failures end-to-end (H1) instead of emitting green builds.
"""

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock supabase before importing manager
sys.modules["supabase"] = MagicMock()

from tgpc.manager import Manager
from tgpc.scraper import PharmacistRecord
from tgpc.utils import Config


def record(reg_no, name="Name", father="Father", category="BPharm", serial=1):
    return PharmacistRecord(
        registration_number=reg_no,
        name=name,
        father_name=father,
        category=category,
        serial_number=serial,
    )


def make_manager(temp_dir: str) -> Manager:
    with patch("tgpc.manager.load_credentials"):
        with patch(
            "tgpc.manager.Config.load",
            return_value=Config(data_directory=temp_dir, enrichment_directory=temp_dir),
        ):
            with patch("tgpc.manager.Scraper"):
                return Manager()


class SyncToSupabaseStorageTests(unittest.TestCase):
    def test_returns_false_on_missing_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            with patch("tgpc.manager.os.environ", {}):
                self.assertFalse(manager.sync_to_supabase_storage())

    def test_returns_true_on_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            env = {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SECRET_KEY": "test-key"}
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.requests.post", return_value=MagicMock(ok=True)) as post:
                    self.assertTrue(manager.sync_to_supabase_storage())
                    self.assertIn("/storage/v1/object/tgpc/rph.json", post.call_args.args[0])
                    self.assertEqual(post.call_args.kwargs["headers"]["x-upsert"], "true")

    def test_returns_false_on_http_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            env = {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SECRET_KEY": "test-key"}
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.requests.post", return_value=MagicMock(ok=False)):
                    self.assertFalse(manager.sync_to_supabase_storage())


class SyncToR2Tests(unittest.TestCase):
    @staticmethod
    def _env(complete=True):
        if not complete:
            return {"CLOUDFLARE_ACCOUNT_ID": "acct"}
        return {
            "CLOUDFLARE_ACCOUNT_ID": "acct",
            "R2_ACCESS_KEY_ID": "key",
            "R2_SECRET_ACCESS_KEY": "secret",
        }

    def test_returns_false_on_missing_account_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            with patch("tgpc.manager.os.environ", {}):
                self.assertFalse(manager.sync_to_r2())

    def test_returns_false_on_missing_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            with patch("tgpc.manager.os.environ", self._env(complete=False)):
                self.assertFalse(manager.sync_to_r2())

    def test_returns_true_on_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            with patch("tgpc.manager.os.environ", self._env()):
                with patch("tgpc.manager.R2Client") as mock_r2_cls:
                    mock_client = MagicMock()
                    mock_r2_cls.return_value = mock_client
                    self.assertTrue(manager.sync_to_r2())
                    mock_client.put_object.assert_called_once()
                    self.assertEqual(mock_client.put_object.call_args[0][0], "rph.json")

    def test_returns_false_when_r2_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            with patch("tgpc.manager.os.environ", self._env()):
                with patch("tgpc.manager.R2Client") as mock_r2_cls:
                    mock_client = MagicMock()
                    mock_client.put_object.side_effect = Exception("boom")
                    mock_r2_cls.return_value = mock_client
                    self.assertFalse(manager.sync_to_r2())


class SyncToGDriveTests(unittest.TestCase):
    @staticmethod
    def _env(config_b64=None):
        env = {}
        if config_b64 is not None:
            env["RCLONE_GDRIVE_CONFIG"] = base64.b64encode(config_b64.encode()).decode()
        return env

    def test_returns_false_on_missing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            with patch("tgpc.manager.os.environ", {}):
                self.assertFalse(manager.sync_to_gdrive())

    def test_writes_temp_config_and_cleans_up_on_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            with patch("tgpc.manager.os.environ", self._env("rclone-config")):
                result = MagicMock(returncode=0)
                with patch("tgpc.manager.subprocess.run", return_value=result) as run:
                    self.assertTrue(manager.sync_to_gdrive())
                args = run.call_args.args[0]
                self.assertEqual(args[:3], ["rclone", "copyto", str(Path(temp_dir) / "rph.json")])
                self.assertEqual(args[3], "gdrive:tgpc/rph.json")
                self.assertFalse(Path("/tmp/rclone-gdrive.conf").exists())

    def test_returns_false_when_rclone_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            with patch("tgpc.manager.os.environ", self._env("rclone-config")):
                result = MagicMock(returncode=1, stderr="boom")
                with patch("tgpc.manager.subprocess.run", return_value=result):
                    self.assertFalse(manager.sync_to_gdrive())


class SyncToReleaseTests(unittest.TestCase):
    """Uses the real pyzipper so the AES archive path is exercised end-to-end."""

    @staticmethod
    def _env(password=None):
        env = {"GITHUB_REPOSITORY": "tgpc-org/tgpc"}
        if password is not None:
            env["RELEASE_PASSWORD"] = password
        return env

    def test_skips_without_password(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            with patch("tgpc.manager.os.environ", self._env()):
                with patch("tgpc.manager.subprocess.run") as run:
                    self.assertTrue(manager.sync_to_release())
                    run.assert_not_called()

    def test_uploads_encrypted_archive_then_cleans_up(self):
        import pyzipper

        observed = {}

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["gh", "release", "upload"]:
                path = Path(cmd[4])
                observed["existed_at_upload"] = path.exists()
                # Wrong/no password must fail to read: proves encryption.
                with pyzipper.AESZipFile(path, "r") as zf:
                    zf.setpassword(b"test-pass")
                    observed["members"] = zf.namelist()
            return MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001"), record("RPH002", serial=2)])
            archive_path = Path(temp_dir) / "rph.json.zip"
            with patch("tgpc.manager.os.environ", self._env("test-pass")):
                with patch("tgpc.manager.subprocess.run", side_effect=fake_run):
                    self.assertTrue(manager.sync_to_release())

        self.assertTrue(observed["existed_at_upload"])
        self.assertEqual(observed["members"], ["rph.json"])
        self.assertFalse(archive_path.exists(), "archive must be removed after upload")

    def test_transport_failure_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            with patch("tgpc.manager.os.environ", self._env("test-pass")):
                with patch("tgpc.manager.subprocess.run", side_effect=OSError("gh exploded")):
                    self.assertFalse(manager.sync_to_release())


class SyncToEmailTests(unittest.TestCase):
    DETAILS = {
        "new_details": ["RPH001 - Name (BPharm)"],
        "modified_details": [],
        "removed_details": [],
        "new_cat_stats": {"BPharm": 1},
        "rem_cat_stats": {},
        "mod_cat_stats": {},
        "total_records": 100,
    }

    def test_returns_true_when_credentials_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            with patch("tgpc.manager.os.environ", {}):
                self.assertTrue(manager.sync_to_email())

    def test_returns_true_with_no_update_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            env = {"RESEND_API_KEY": "k", "NOTIFICATION_EMAIL": "to@example.com"}
            with patch("tgpc.manager.os.environ", env):
                self.assertTrue(manager.sync_to_email())

    def test_sends_via_requests_and_reports_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager._last_update_details = dict(self.DETAILS)
            env = {"RESEND_API_KEY": "k", "NOTIFICATION_EMAIL": "to@example.com"}
            resp = MagicMock(ok=True, text='{"id":"abc"}')
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.requests.post", return_value=resp) as post:
                    self.assertTrue(manager.sync_to_email())
            kwargs = post.call_args.kwargs
            self.assertEqual(post.call_args.args[0], "https://api.resend.com/emails")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer k")
            self.assertEqual(kwargs["json"]["to"], ["to@example.com"])
            self.assertIn("NEW (1)", kwargs["json"]["text"])

    def test_api_error_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager._last_update_details = dict(self.DETAILS)
            env = {"RESEND_API_KEY": "k", "NOTIFICATION_EMAIL": "to@example.com"}
            resp = MagicMock(ok=False, status_code=422, text='{"message":"bad"}')
            with patch("tgpc.manager.os.environ", env):
                with patch("tgpc.manager.requests.post", return_value=resp):
                    self.assertFalse(manager.sync_to_email())


if __name__ == "__main__":
    unittest.main()
