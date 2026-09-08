import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

# Mock supabase before importing manager
sys.modules["supabase"] = MagicMock()

from tgpc.manager import Manager
from tgpc.scraper import PharmacistRecord
from tgpc.utils import Config, TGPCError


class FakeScraper:
    def __init__(self, records):
        self._records = records

    def health_check(self):
        return True

    def extract_basic_records(self):
        return self._records


class FailingScraper:
    def __init__(self, error):
        self._error = error

    def health_check(self):
        return True

    def extract_basic_records(self):
        raise self._error


def record(reg_no, name, father, category, serial):
    return PharmacistRecord(
        registration_number=reg_no,
        name=name,
        father_name=father,
        category=category,
        serial_number=serial,
    )


class ManagerUpdateTests(unittest.TestCase):
    def setUp(self):
        # Tiny fixtures are valid in tests; never touch production R2/Supabase.
        os.environ["TGPC_ALLOW_SMALL_RPH"] = "1"
        p1 = patch("tgpc.manager.BackupManager._upload_to_r2", return_value=True)
        p2 = patch("tgpc.manager.Manager._restore_rph_from_backup", return_value=True)
        self._upload_mock = p1.start()
        self._restore_mock = p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.addCleanup(os.environ.pop, "TGPC_ALLOW_SMALL_RPH", None)

    def _make_manager(self, temp_dir: str, fresh_records):
        with patch("tgpc.manager.Config.load", return_value=Config(data_directory=temp_dir)):
            with patch("tgpc.manager.Scraper", return_value=FakeScraper(fresh_records)):
                return Manager()

    def _make_manager_with_scraper(self, temp_dir: str, scraper):
        with patch("tgpc.manager.Config.load", return_value=Config(data_directory=temp_dir)):
            with patch("tgpc.manager.Scraper", return_value=scraper):
                return Manager()

    def test_safety_guard_blocks_large_drop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = [record(f"RPH{i:03d}", f"Name{i}", f"Father{i}", "BPharm", i) for i in range(1, 101)]
            fresh = [record(f"RPH{i:03d}", f"Name{i}", f"Father{i}", "BPharm", i) for i in range(1, 81)]

            manager = self._make_manager(temp_dir, fresh)
            manager.file_manager.save(existing)

            manager.run_daily_update()

            final_records = manager.file_manager.load()
            self.assertEqual(len(final_records), 100)
            self.assertEqual(final_records[0].registration_number, "RPH001")
            self.assertEqual(final_records[-1].registration_number, "RPH100")

    def test_update_writes_sorted_deduped_data_and_github_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = [
                record("A1", "Alpha Old", "F1", "BPharm", 2),
                record("B2", "Beta", "F2", "DPharm", 4),
                record("D4", "Delta", "F4", "QP", 8),
            ]
            fresh = [
                record("B2", "Beta", "F2", "DPharm", 5),
                record("A1", "Alpha New", "F1", "BPharm", 3),
                record("C3", "Gamma", "F3", "PharmD", 1),
                record("B2", "Beta", "F2", "DPharm", 4),  # duplicate
            ]

            manager = self._make_manager(temp_dir, fresh)
            manager.file_manager.save(existing)

            with tempfile.NamedTemporaryFile(delete=False) as out_file:
                github_output = out_file.name

            old_env = os.environ.get("GITHUB_OUTPUT")
            try:
                os.environ["GITHUB_OUTPUT"] = github_output
                manager.run_daily_update()
            finally:
                if old_env is None:
                    os.environ.pop("GITHUB_OUTPUT", None)
                else:
                    os.environ["GITHUB_OUTPUT"] = old_env

            saved = json.loads(Path(temp_dir, "rph.json").read_text(encoding="utf-8"))
            self.assertEqual([r["registration_number"] for r in saved], ["C3", "A1", "B2"])
            self.assertEqual(saved[1]["name"], "Alpha New")

            output_lines = Path(github_output).read_text(encoding="utf-8").splitlines()
            output = {}
            for line in output_lines:
                if "=" in line:
                    k, v = line.split("=", 1)
                    output[k] = v

            self.assertEqual(output["total_records"], "3")
            self.assertEqual(output["new_records"], "1")
            self.assertEqual(output["removed_records"], "1")
            self.assertEqual(output["modified_records"], "1")
            self.assertEqual(output["duplicates_removed"], "1")
            self.assertEqual(output["success"], "True")

            self.assertEqual(json.loads(output["new_cat_stats"]), {"PharmD": 1})
            self.assertEqual(json.loads(output["rem_cat_stats"]), {"QP": 1})
            self.assertEqual(json.loads(output["mod_cat_stats"]), {"BPharm": 1})

            os.unlink(github_output)

    def test_update_outputs_deterministic_detail_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = [
                record("R2", "Removed Two", "F2", "DPharm", 2),
                record("R4", "Kept Four", "F4", "BPharm", 4),
                record("R6", "Kept Six", "F6", "QP", 6),
                record("R8", "Removed Eight", "F8", "PharmD", 8),
            ]
            fresh = [
                record("R6", "Updated Six", "F6", "QC", 6),
                record("R3", "New Three", "F3", "DPharm", 3),
                record("R1", "New One", "F1", "BPharm", 1),
                record("R4", "Updated Four", "F4", "MPharm", 4),
            ]

            manager = self._make_manager(temp_dir, fresh)
            manager.file_manager.save(existing)

            with tempfile.NamedTemporaryFile(delete=False) as out_file:
                github_output = out_file.name

            old_env = os.environ.get("GITHUB_OUTPUT")
            try:
                os.environ["GITHUB_OUTPUT"] = github_output
                manager.run_daily_update()
            finally:
                if old_env is None:
                    os.environ.pop("GITHUB_OUTPUT", None)
                else:
                    os.environ["GITHUB_OUTPUT"] = old_env

            output_lines = Path(github_output).read_text(encoding="utf-8").splitlines()
            output = {}
            for line in output_lines:
                if "=" in line:
                    k, v = line.split("=", 1)
                    output[k] = v

            self.assertEqual(
                output["new_details"],
                json.dumps(
                    [
                        "R1 - New One (BPharm)",
                        "R3 - New Three (DPharm)",
                    ]
                ),
            )
            self.assertEqual(
                output["removed_details"],
                json.dumps(
                    [
                        "R2 - Removed Two (DPharm)",
                        "R8 - Removed Eight (PharmD)",
                    ]
                ),
            )
            self.assertEqual(
                output["modified_details"],
                json.dumps(
                    [
                        "R4 - Updated Four (MPharm)",
                        "R6 - Updated Six (QC)",
                    ]
                ),
            )

            self.assertEqual(output["new_cat_stats"], json.dumps({"BPharm": 1, "DPharm": 1}))
            self.assertEqual(output["rem_cat_stats"], json.dumps({"DPharm": 1, "PharmD": 1}))
            self.assertEqual(output["mod_cat_stats"], json.dumps({"MPharm": 1, "QC": 1}))

            os.unlink(github_output)

    def test_update_soft_skips_when_source_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = [
                record("RPH001", "Existing", "Parent", "BPharm", 1),
            ]
            scraper = FailingScraper(
                TGPCError(
                    "Request failed",
                    requests.exceptions.ConnectTimeout("source timed out"),
                )
            )
            manager = self._make_manager_with_scraper(temp_dir, scraper)
            manager.file_manager.save(existing)

            with tempfile.NamedTemporaryFile(delete=False) as out_file:
                github_output = out_file.name

            old_env = os.environ.get("GITHUB_OUTPUT")
            try:
                os.environ["GITHUB_OUTPUT"] = github_output
                status = manager.run_daily_update()
            finally:
                if old_env is None:
                    os.environ.pop("GITHUB_OUTPUT", None)
                else:
                    os.environ["GITHUB_OUTPUT"] = old_env

            self.assertEqual(status, "source_unavailable")
            saved = json.loads(Path(temp_dir, "rph.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, [existing[0].to_dict()])

            output_lines = Path(github_output).read_text(encoding="utf-8").splitlines()
            output = {}
            for line in output_lines:
                if "=" in line:
                    k, v = line.split("=", 1)
                    output[k] = v

            self.assertEqual(output["update_status"], "source_unavailable")
            self.assertEqual(output["source_error"], "ConnectTimeout")
            self.assertEqual(output["success"], "False")
            self.assertEqual(output["total_records"], "1")
            self.assertEqual(output["new_details"], "[]")
            self.assertEqual(output["mod_cat_stats"], "{}")

            os.unlink(github_output)

    def test_sync_to_supabase_returns_false_on_missing_credentials(self):
        """H1: sync methods must return a status so callers can propagate failures."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("tgpc.manager.load_credentials"):
                with patch(
                    "tgpc.manager.Config.load",
                    return_value=Config(data_directory=temp_dir, enrichment_directory=temp_dir),
                ):
                    manager = Manager()
            with patch("tgpc.manager.os.environ", {}):
                self.assertFalse(manager.sync_to_supabase())

    def test_sync_to_supabase_returns_true_on_success(self):
        """H1: a completed sync must report success."""
        with tempfile.TemporaryDirectory():
            with patch("tgpc.manager.load_credentials"):
                manager = Manager()
            with patch(
                "tgpc.manager.os.environ",
                {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SECRET_KEY": "test-key"},
            ):
                fake_client = MagicMock()
                fake_client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
                fake_client.table.return_value.select.return_value.count = 0
                manager.file_manager.save([record("RPH001", "Name", "Father", "BPharm", 1)])
                with patch("tgpc.manager.create_client", return_value=fake_client):
                    self.assertTrue(manager.sync_to_supabase())

    def test_sync_to_r2_returns_false_on_missing_credentials(self):
        """H1: R2 sync reports failure rather than returning None."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("tgpc.manager.load_credentials"):
                with patch(
                    "tgpc.manager.Config.load",
                    return_value=Config(data_directory=temp_dir, enrichment_directory=temp_dir),
                ):
                    manager = Manager()
            with patch("tgpc.manager.os.environ", {}):
                self.assertFalse(manager.sync_to_r2())


if __name__ == "__main__":
    unittest.main()
