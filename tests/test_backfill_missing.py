"""Backfill tests: blank-only column fills from the live source.

Locks the safety contract — a re-scrape may fill blanks but must never
overwrite an existing value, blank a column, or patch a record whose
identity differs from the source.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Mock supabase before importing the module under test
sys.modules["supabase"] = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()

from tgpc import backfill_missing


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.cols = None
        self._in = None
        self._update = None
        self._eq = None

    def select(self, cols):
        self.cols = [c.strip() for c in cols.split(",")]
        return self

    def in_(self, _col, values):
        self._in = list(values)
        return self

    def update(self, patch):
        self._update = dict(patch)
        return self

    def eq(self, _col, value):
        self._eq = value
        return self

    def execute(self):
        rows = self.client.store
        if self._update is not None:
            self.client.patches.append((self._eq, dict(self._update)))
            if self._eq in rows:
                rows[self._eq].update(self._update)
            return SimpleNamespace(data=[])
        picked = self._in or list(rows)
        return SimpleNamespace(data=[{k: rows[r].get(k) for k in self.cols} for r in picked if r in rows])


class FakeClient:
    def __init__(self, store):
        self.store = store
        self.patches = []

    def table(self, name):
        return FakeQuery(self, name)


def make_record(**kw):
    base = {"gender": None, "validity_date": None, "name": ""}
    base.update(kw)
    return SimpleNamespace(**base)


class BackfillTests(unittest.TestCase):
    def run_backfill(self, store, source, ids):
        """source: reg -> record-or-None. Returns (store, patches, fake)."""
        fake = FakeClient(store)
        scraped = []

        class FakeScraper:
            def __init__(self):
                self.rate_limiter = SimpleNamespace(min_delay=1.0, current_delay=1.0)

            def extract_detailed_info(self, reg_no, img_dir=None):
                scraped.append(reg_no)
                return source.get(reg_no)

        with tempfile.TemporaryDirectory() as tmp:
            ids_file = Path(tmp) / "ids.jsonl"
            ids_file.write_text("\n".join(json.dumps({"registration_number": r}) for r in ids) + "\n")
            argv = ["backfill", "--ids-file", str(ids_file), "--workers", "1", "--min-delay", "0"]
            with patch.object(sys, "argv", argv):
                with patch.object(backfill_missing, "Scraper", FakeScraper):
                    with patch.object(backfill_missing, "create_client", return_value=fake):
                        with patch.object(backfill_missing, "load_credentials", lambda: None):
                            with patch.dict("os.environ", {"SUPABASE_URL": "u", "SUPABASE_SECRET_KEY": "k"}):
                                backfill_missing.main()
        return store, fake.patches

    def test_fills_only_blank_columns(self):
        store = {
            "A": {"registration_number": "A", "name": "Ann", "gender": None, "validity_date": "31-Dec-2030"},
            "B": {"registration_number": "B", "name": "Bob", "gender": "Male", "validity_date": None},
        }
        source = {
            "A": make_record(name="Ann", gender="Female", validity_date="31-Dec-2031"),
            # Source disagrees on B's gender, but B already has one: never overwrite.
            "B": make_record(name="Bob", gender="Female", validity_date="31-Dec-2029"),
        }
        result, patches = self.run_backfill(store, source, ["A", "B"])
        sent = dict(patches)
        self.assertEqual(sent["A"], {"gender": "Female"})
        self.assertEqual(sent["B"], {"validity_date": "31-Dec-2029"})
        self.assertEqual(result["B"]["gender"], "Male")  # untouched

    def test_never_writes_blank_values(self):
        store = {"C": {"registration_number": "C", "name": "Cara", "gender": None, "validity_date": None}}
        source = {"C": make_record(name="Cara", gender="", validity_date="   ")}
        result, patches = self.run_backfill(store, source, ["C"])
        self.assertEqual(patches, [])
        self.assertIsNone(result["C"]["gender"])

    def test_skips_identity_mismatch(self):
        store = {"D": {"registration_number": "D", "name": "Dana", "gender": None, "validity_date": None}}
        source = {"D": make_record(name="Someone Else", gender="Female", validity_date=None)}
        _result, patches = self.run_backfill(store, source, ["D"])
        self.assertEqual(patches, [])

    def test_skips_missing_source_record(self):
        store = {"E": {"registration_number": "E", "name": "Eve", "gender": None, "validity_date": None}}
        _result, patches = self.run_backfill(store, {"E": None}, ["E"])
        self.assertEqual(patches, [])

    def test_skips_registration_not_in_db(self):
        store = {"F": {"registration_number": "F", "name": "Fay", "gender": None, "validity_date": None}}
        source = {"ZZ": make_record(name="Zed", gender="Male")}
        _result, patches = self.run_backfill(store, source, ["ZZ"])
        self.assertEqual(patches, [])


if __name__ == "__main__":
    unittest.main()
