"""Merge-refresh tests: post-scrape recompute of DG-merged columns.

Covers the fail-closed contract (missing creds / transport failure warn
instead of raising), the fill/newer-wins rules, and that upserts carry only
changed columns for changed rows.
"""

import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Mock supabase before importing manager
sys.modules["supabase"] = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()

from tgpc.manager import Manager
from tgpc.utils import Config


def make_manager(temp_dir: str) -> Manager:
    with patch("tgpc.manager.load_credentials"):
        with patch(
            "tgpc.manager.Config.load",
            return_value=Config(data_directory=temp_dir, enrichment_directory=temp_dir),
        ):
            with patch("tgpc.manager.Scraper"):
                return Manager()


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.cols = None
        self.slice = (0, 2000)

    def select(self, cols):
        self.cols = [c.strip() for c in cols.split(",")]
        return self

    def order(self, _col):
        return self

    def range(self, a, b):
        self.slice = (a, b)
        return self

    def execute(self):
        if self.cols is None:
            return SimpleNamespace(data=[])
        rows = self.client.store.get(self.table, [])
        lo, hi = self.slice
        return SimpleNamespace(data=[{k: r.get(k) for k in self.cols} for r in rows[lo : hi + 1]])

    def upsert(self, batch, on_conflict=None):
        self.client.upserts.append((self.table, batch, on_conflict))
        return self


class FakeClient:
    def __init__(self, store):
        self.store = store
        self.upserts = []

    def table(self, name):
        return FakeQuery(self, name)


DG = [
    {
        "registration_number": "FILL",
        "date_of_registration": "01-08-2018",
        "home_state": "telangana",
        "renewal_validity": "31-12-2027",
    },
    {
        "registration_number": "NEWER",
        "date_of_registration": "03-08-2018",
        "home_state": "KARNATAKA",
        "renewal_validity": "31-12-2030",
    },
    {
        "registration_number": "OLDER",
        "date_of_registration": "03-08-2018",
        "home_state": "",
        "renewal_validity": "31-12-2020",
    },
    {
        "registration_number": "CLEAN",
        "date_of_registration": "03-08-2018",
        "home_state": "TELANGANA",
        "renewal_validity": "31-12-2027",
    },
    {
        "registration_number": "BADDATE",
        "date_of_registration": "03-08-2018",
        "home_state": "",
        "renewal_validity": "31-12-2027",
    },
]

RPH = [
    {
        "registration_number": "FILL",
        "date_of_registration": "",
        "home_state": "",
        "validity_date": "",
    },
    {
        "registration_number": "NEWER",
        "date_of_registration": "03-08-2018",
        "home_state": "TELANGANA",
        "validity_date": "31-Dec-2022",
    },
    {
        "registration_number": "OLDER",
        "date_of_registration": "03-08-2018",
        "home_state": "TELANGANA",
        "validity_date": "31-Dec-2027",
    },
    {
        "registration_number": "CLEAN",
        "date_of_registration": "03-08-2018",
        "home_state": "TELANGANA",
        "validity_date": "31-Dec-2027",
    },
    {
        "registration_number": "NODG",
        "date_of_registration": "",
        "home_state": "",
        "validity_date": "",
    },
    {
        "registration_number": "BADDATE",
        "date_of_registration": "",
        "home_state": "",
        "validity_date": "not-a-date",
    },
]


class RefreshMergedColumnsTests(unittest.TestCase):
    def run_refresh(self, temp_dir, store):
        manager = make_manager(temp_dir)
        fake = FakeClient(store)
        env = {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SECRET_KEY": "k"}
        with patch("tgpc.manager.os.environ", env):
            with patch("tgpc.manager.create_client", return_value=fake):
                stats = manager.refresh_merged_columns()
        return stats, fake

    def test_fills_newerwins_and_skips(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stats, fake = self.run_refresh(temp_dir, {"rph_dg_contacts": DG, "rph": RPH})
            self.assertEqual(stats["error"], "")
            self.assertEqual(stats["dor_filled"], 2)  # FILL + BADDATE
            self.assertEqual(stats["home_filled"], 1)  # FILL only (upper-cased)
            self.assertEqual(stats["validity_updated"], 2)  # FILL blank + NEWER
            self.assertEqual(stats["review_skipped"], 1)  # BADDATE unparseable
            # Only changed rows upserted, carrying only changed columns + key
            sent = {}
            for _table, batch, conflict in fake.upserts:
                self.assertEqual(conflict, "registration_number")
                for row in batch:
                    sent[row["registration_number"]] = row
            self.assertEqual(set(sent), {"FILL", "NEWER", "BADDATE"})
            self.assertEqual(
                sent["FILL"],
                {
                    "registration_number": "FILL",
                    "date_of_registration": "01-08-2018",
                    "home_state": "TELANGANA",
                    "validity_date": "31-Dec-2027",
                },
            )
            self.assertEqual(sent["NEWER"], {"registration_number": "NEWER", "validity_date": "31-Dec-2030"})
            self.assertEqual(
                sent["BADDATE"],
                {"registration_number": "BADDATE", "date_of_registration": "03-08-2018"},
            )

    def test_fails_closed_on_missing_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            with patch("tgpc.manager.os.environ", {}):
                stats = manager.refresh_merged_columns()
            self.assertNotEqual(stats["error"], "")
            self.assertEqual(stats["dor_filled"], 0)

    def test_empty_tables_upsert_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stats, fake = self.run_refresh(temp_dir, {"rph_dg_contacts": [], "rph": []})
            self.assertEqual(stats["error"], "")
            self.assertEqual(fake.upserts, [])


if __name__ == "__main__":
    unittest.main()
