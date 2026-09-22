"""Photo-pipeline tests (CODE_REVIEW.md T3 remainder).

Covers the paths around `_process_records_sequential` that the M2 corruption
bug lived beside: R2 upload + verify + local delete, failure keeping files,
per-record error isolation inside a batch, `retry_photos`, and the
`upload_and_verify_photo` retry contract.
"""

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


def detail(reg_no, name="Name", father="Father", category="BPharm"):
    """A scraped detail result matching its basic record (passes validation)."""
    return PharmacistRecord(
        registration_number=reg_no,
        name=name,
        father_name=father,
        category=category,
        gender="Male",
        status="Active",
        validity_date="31-Dec-2026",
    )


class PhotoPathTests(unittest.TestCase):
    def _make_manager(self, temp_dir: str):
        enter = self.enterContext
        enter(
            patch(
                "tgpc.manager.Config.load",
                return_value=Config(data_directory=temp_dir, enrichment_directory=temp_dir),
            )
        )
        enter(patch("tgpc.manager.load_credentials"))
        return Manager()

    def _fake_supabase(self):
        client = MagicMock()
        client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        return client

    # --- _process_records_sequential ---------------------------------

    @patch("tgpc.manager.os.environ", {"CLOUDFLARE_ACCOUNT_ID": "acct"})
    @patch.object(Manager, "_upload_photo_to_r2", return_value=True)
    @patch.object(Manager, "_verify_photo_on_r2", return_value=True)
    def test_photo_uploaded_verified_then_local_deleted(self, _verify, _upload):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            manager.scraper = MagicMock()
            manager.scraper.extract_detailed_info.return_value = detail("RPH001")

            img_dir = Path(temp_dir) / "webp"
            img_dir.mkdir()
            photo = img_dir / "RPH001.webp"
            photo.write_bytes(b"webp-bytes")

            supabase = self._fake_supabase()
            processed = manager._process_records_sequential(
                [record("RPH001")],
                {record("RPH001").registration_number: record("RPH001")},
                img_dir,
                supabase=supabase,
            )

            self.assertEqual(processed, 1)
            self.assertFalse(photo.exists(), "local photo must be deleted after verified upload")
            data = supabase.table.return_value.upsert.call_args.args[0]
            self.assertEqual(data["registration_number"], "RPH001")
            self.assertTrue(data["photo_url"].endswith("/photos/RPH001.webp"))
            self.assertEqual(data["gender"], "Male")

    @patch("tgpc.manager.os.environ", {"CLOUDFLARE_ACCOUNT_ID": "acct"})
    @patch.object(Manager, "upload_and_verify_photo", return_value=False)
    def test_failed_upload_keeps_local_photo_and_still_upserts(self, _uav):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            manager.scraper = MagicMock()
            manager.scraper.extract_detailed_info.return_value = detail("RPH001")

            img_dir = Path(temp_dir) / "webp"
            img_dir.mkdir()
            photo = img_dir / "RPH001.webp"
            photo.write_bytes(b"webp-bytes")

            supabase = self._fake_supabase()
            processed = manager._process_records_sequential(
                [record("RPH001")], {"RPH001": record("RPH001")}, img_dir, supabase=supabase
            )

            self.assertEqual(processed, 1)
            self.assertTrue(photo.exists(), "failed upload must keep the local file")
            data = supabase.table.return_value.upsert.call_args.args[0]
            self.assertEqual(data["photo_url"], "", "no R2 URL may be set when the upload failed")

    def test_record_scrape_failure_does_not_abort_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)

            def scrape(reg_no, img_dir=None):
                if reg_no == "RPH001":
                    raise RuntimeError("source hiccup")
                return detail("RPH002")

            manager.scraper = MagicMock()
            manager.scraper.extract_detailed_info.side_effect = scrape

            img_dir = Path(temp_dir) / "webp"
            img_dir.mkdir()
            supabase = self._fake_supabase()
            processed = manager._process_records_sequential(
                [record("RPH001"), record("RPH002", serial=2)],
                {"RPH001": record("RPH001"), "RPH002": record("RPH002", serial=2)},
                img_dir,
                supabase=supabase,
            )

            self.assertEqual(processed, 1)
            upserted = supabase.table.return_value.upsert.call_args.args[0]
            self.assertEqual(upserted["registration_number"], "RPH002")

    def test_supabase_upsert_failure_does_not_abort_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            manager.scraper = MagicMock()
            manager.scraper.extract_detailed_info.side_effect = lambda reg, img=None: detail(reg)

            img_dir = Path(temp_dir) / "webp"
            img_dir.mkdir()
            supabase = self._fake_supabase()
            supabase.table.return_value.upsert.return_value.execute.side_effect = [
                RuntimeError("network"),
                MagicMock(data=[]),
            ]
            processed = manager._process_records_sequential(
                [record("RPH001"), record("RPH002", serial=2)],
                {"RPH001": record("RPH001"), "RPH002": record("RPH002", serial=2)},
                img_dir,
                supabase=supabase,
            )
            self.assertEqual(processed, 2)

    # --- upload_and_verify_photo retry contract ----------------------

    @patch("time.sleep")
    def test_upload_and_verify_succeeds_immediately(self, mock_sleep):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            photo = Path(temp_dir) / "x.webp"
            photo.write_bytes(b"x" * 200)
            with (
                patch.object(manager, "_upload_photo_to_r2", return_value=True),
                patch.object(manager, "_verify_photo_on_r2", return_value=True),
            ):
                self.assertTrue(manager.upload_and_verify_photo(photo, "photos/x.webp"))
            mock_sleep.assert_not_called()

    @patch("time.sleep")
    def test_upload_and_verify_retries_then_succeeds(self, mock_sleep):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            photo = Path(temp_dir) / "x.webp"
            photo.write_bytes(b"x" * 200)
            with (
                patch.object(manager, "_upload_photo_to_r2", side_effect=[False, True]),
                patch.object(manager, "_verify_photo_on_r2", return_value=True),
            ):
                self.assertTrue(manager.upload_and_verify_photo(photo, "photos/x.webp"))
            mock_sleep.assert_called_once_with(2)

    @patch("time.sleep")
    def test_upload_and_verify_gives_up_after_max_retries(self, mock_sleep):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            photo = Path(temp_dir) / "x.webp"
            photo.write_bytes(b"x" * 200)
            with (
                patch.object(manager, "_upload_photo_to_r2", return_value=True),
                patch.object(manager, "_verify_photo_on_r2", return_value=False) as verify,
            ):
                self.assertFalse(manager.upload_and_verify_photo(photo, "photos/x.webp"))
            self.assertEqual(verify.call_count, 5)
            self.assertEqual(mock_sleep.call_count, 4)

    # --- _verify_photo_on_r2 size check -------------------------------

    @patch(
        "tgpc.manager.os.environ",
        {"CLOUDFLARE_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s"},
    )
    def test_verify_accepts_matching_size(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            with patch("tgpc.manager.R2Client") as mock_r2_cls:
                mock_client = MagicMock()
                mock_client.head_object.return_value = {"ContentLength": 123}
                mock_r2_cls.return_value = mock_client
                self.assertTrue(manager._verify_photo_on_r2("photos/a.webp", 123))

    @patch(
        "tgpc.manager.os.environ",
        {"CLOUDFLARE_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s"},
    )
    def test_verify_rejects_size_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            with patch("tgpc.manager.R2Client") as mock_r2_cls:
                mock_client = MagicMock()
                mock_client.head_object.return_value = {"ContentLength": 456}
                mock_r2_cls.return_value = mock_client
                self.assertFalse(manager._verify_photo_on_r2("photos/a.webp", 123))

    # --- retry_photos --------------------------------------------------

    def test_retry_photos_deletes_successes_keeps_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            webp_dir = Path(temp_dir) / "webp"
            webp_dir.mkdir()
            # sorted() order: BAD1 is processed (and deleted) first
            bad = webp_dir / "BAD1.webp"
            good = webp_dir / "GOOD1.webp"
            bad.write_bytes(b"b")
            good.write_bytes(b"g")
            with patch.object(manager, "upload_and_verify_photo", side_effect=[True, False]) as uav:
                manager.retry_photos()

            self.assertFalse(bad.exists(), "verified upload must delete local file")
            self.assertTrue(good.exists(), "failed upload must keep local file")
            keys = [c.args[1] for c in uav.call_args_list]
            self.assertEqual(keys, ["photos/BAD1.webp", "photos/GOOD1.webp"])

    def test_retry_photos_noop_without_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._make_manager(temp_dir)
            with patch.object(manager, "upload_and_verify_photo") as uav:
                manager.retry_photos()  # must not raise
            uav.assert_not_called()


if __name__ == "__main__":
    unittest.main()
