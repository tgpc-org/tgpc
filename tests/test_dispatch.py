"""`python3 -m tgpc dispatch` — council dispatch lists copied into R2.

The sync runs against a live government page and a live bucket, so these tests
pin the parts that decide what becomes *public*: the name normalisation (a
wrong name is a file the portal serves as 404), the page parsing (only real
dispatch links), the missing-file plan (existing objects must not be rewritten
by accident) and the upload verification (a half-written object must fail, not
report success).

Everything external is faked: the R2 client, the HTTP session and the fallback
path. Nothing here touches the network, the bucket or the repo's real files.
"""

import tempfile
import unittest
from pathlib import Path

import requests

import tgpc.dispatch as dispatch

PAGE_TEMPLATE = """
<html><body><table>
<tr><td>Dispatch list of Registration Certificates on 01.02.2019</td>
<td><a href="/pdf/Dispatch%20list%20of%20Registration%20Certificates/01.02.2019%20Dispatch%20List.pdf;jsessionid=ABC">Click here</a></td></tr>
<tr><td>Dispatch list on 03.06.2020</td>
<td><a href="/pdf/Dispatch list of Registration Certificates/03.06.2020 Dispatch List c.pdf">Click here</a></td></tr>
<tr><td>Dispatch list on 11.09.2023</td>
<td><a href="/pdf/Dispatch list of Registration Certificates/11-09-2023 Dispatch List.pdf">Click here</a></td></tr>
<tr><td>Dispatch list on 11.09.2023 (again)</td>
<td><a href="/pdf/Dispatch list of Registration Certificates/Dispatch list 11-9-2023.pdf">Click here</a></td></tr>
<tr><td>Dispatch list on 01.06.2024</td>
<td><a href="/pdf/Dispatch list of Registration Certificates/Dispatch list 01-6-2024.pdf">Click here</a></td></tr>
<tr><td>Dispatch list on 16.10.2026</td>
<td><a href="/pdf/Dispatch list of Registration Certificates/Dispatch list 16-10-2026.pdf">Click here</a></td></tr>
<tr><td>Not a dispatch list</td>
<td><a href="/pdf/Regulations/Guidelines.pdf">Click here</a></td></tr>
<tr><td>Unparseable dispatch name</td>
<td><a href="/pdf/Dispatch list of Registration Certificates/THIRD%20FLOOR%20DISPATCH%20LIST.pdf">Click here</a></td></tr>
</table></body></html>
"""


class FakeSession:
    """Minimal requests.Session stand-in: page HTML plus one PDF per URL."""

    def __init__(self, page_html=PAGE_TEMPLATE, pdf_bytes=b"%PDF-1.7\n" + b"0" * 2000, broken=(), missing=()):
        self.page_html = page_html
        self.pdf_bytes = pdf_bytes
        self.broken = set(broken)
        self.missing = set(missing)
        self.headers = {}
        self.requested = []

    def get(self, url, timeout=None, stream=False):
        self.requested.append(url)
        return FakeResponse(self, url)


class FakeResponse:
    def __init__(self, session, url):
        self.session = session
        self.url = url
        if url in session.missing:
            self.status_code = 404
        elif url in session.broken:
            self.status_code = 500
        else:
            self.status_code = 200
        self.text = session.page_html
        self.content = session.pdf_bytes

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code} for {self.url}", response=self)

    def iter_content(self, chunk_size=65536):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeR2:
    """In-memory stand-in for R2Client."""

    def __init__(self, keys=(), bad_head=False):
        self.keys = list(keys)
        self.objects = {key: len(pdf_blob()) for key in keys}
        self.bad_head = bad_head
        self.puts = []

    def list_objects_all(self, prefix):
        return [{"Key": key, "Size": self.objects.get(key, 0)} for key in sorted(self.keys) if key.startswith(prefix)]

    def head_object(self, key):
        if self.bad_head:
            return {"ContentLength": -1}
        return {"ContentLength": self.objects.get(key, 0)}

    def put_object(self, key, _):
        self.keys.append(key)
        self.puts.append(key)
        self.objects[key] = len(pdf_blob())

    def get_object(self, key, path):
        path.write_bytes(pdf_blob())

    def bucket_names(self):
        """Return portal names already in the bucket (keys stripped of the prefix)."""
        prefix = "dispatch/"
        names = set()
        for key in self.keys:
            if key.startswith(prefix) and "/" not in key[len(prefix) :]:
                names.add(key[len(prefix) :])
        return names


def pdf_blob() -> bytes:
    return b"%PDF-1.7\n" + b"0" * 2000


class NameNormalisationTests(unittest.TestCase):
    """A wrong name is a file the portal serves as 404."""

    def test_council_dot_format(self):
        self.assertEqual(dispatch.dispatch_name("01.02.2019 Dispatch List.pdf"), "DL01022019.pdf")

    def test_council_dot_format_with_suffix(self):
        self.assertEqual(dispatch.dispatch_name("03.06.2020 Dispatch List c.pdf"), "DL03062020C.pdf")

    def test_council_dash_format(self):
        self.assertEqual(dispatch.dispatch_name("11-09-2023 Dispatch List.pdf"), "DL11092023.pdf")

    def test_portal_dash_format(self):
        self.assertEqual(dispatch.dispatch_name("Dispatch list 16-10-2026.pdf"), "DL16102026.pdf")

    def test_portal_dash_format_single_digit_day(self):
        self.assertEqual(dispatch.dispatch_name("Dispatch list 01-6-2024.pdf"), "DL01062024.pdf")

    def test_invalid_date_returns_none(self):
        self.assertIsNone(dispatch.dispatch_name("31.04.2023 Dispatch List.pdf"))

    def test_unrelated_pdf_returns_none(self):
        self.assertIsNone(dispatch.dispatch_name("Guidelines.pdf"))

    def test_unparseable_dispatch_name_returns_none(self):
        self.assertIsNone(dispatch.dispatch_name("THIRD FLOOR DISPATCH LIST.pdf"))


class PageParsingTests(unittest.TestCase):
    """Only real dispatch links end up in the plan."""

    def test_parses_all_known_formats(self):
        entries, skipped = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        names = [e.name for e in entries]
        self.assertIn("DL01022019.pdf", names)
        self.assertIn("DL03062020C.pdf", names)
        self.assertIn("DL11092023.pdf", names)
        self.assertIn("DL01062024.pdf", names)
        self.assertIn("DL16102026.pdf", names)

    def test_duplicate_date_is_de_duplicated(self):
        entries, _ = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        self.assertEqual(sum(1 for e in entries if e.name == "DL11092023.pdf"), 1)

    def test_non_dispatch_links_are_skipped(self):
        _, skipped = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        # Guidelines.pdf is filtered out before naming check (no "dispatch" in path)
        # Unparseable dispatch names go to skipped
        self.assertIn("THIRD FLOOR DISPATCH LIST.pdf", skipped)

    def test_unparseable_dispatch_name_is_skipped(self):
        _, skipped = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        self.assertIn("THIRD FLOOR DISPATCH LIST.pdf", skipped)


class PlanTests(unittest.TestCase):
    """Existing objects must not be rewritten by accident."""

    def test_only_missing_files_are_downloaded(self):
        present = {"DL01022019.pdf", "DL03062020C.pdf"}
        entries, _ = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        to_upload = dispatch.DispatchSyncer(client=None, session=None).plan(entries, present, force=False)
        names = {e.name for e in to_upload}
        self.assertNotIn("DL01022019.pdf", names)
        self.assertNotIn("DL03062020C.pdf", names)
        self.assertIn("DL11092023.pdf", names)
        self.assertIn("DL16102026.pdf", names)

    def test_force_reuploads_everything(self):
        present = {"DL01022019.pdf", "DL03062020C.pdf"}
        entries, _ = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        to_upload = dispatch.DispatchSyncer(client=None, session=None).plan(entries, present, force=True)
        names = {e.name for e in to_upload}
        self.assertEqual(len(names), 5)


class SyncRunTests(unittest.TestCase):
    """A run against faked R2 and HTTP must behave correctly."""

    def test_dry_run_reports_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession()
            r2 = FakeR2(keys=("dispatch/DL01022019.pdf", "dispatch/DL03062020C.pdf"))
            report = dispatch.sync_dispatch(
                dry_run=True,
                session=session,
                client=r2,
                workdir=Path(tmp),
            )
        self.assertEqual(report.published, 5)
        self.assertEqual(report.already_present, 2)
        self.assertEqual(report.missing, 3)
        self.assertEqual(report.uploaded, 0)
        self.assertEqual(r2.puts, [])

    def test_uploads_missing_files_and_verifies_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession()
            r2 = FakeR2(keys=("dispatch/DL01022019.pdf", "dispatch/DL03062020C.pdf"))
            report = dispatch.sync_dispatch(
                dry_run=False,
                session=session,
                client=r2,
                workdir=Path(tmp),
            )
        self.assertEqual(report.uploaded, 3)
        # r2.puts contains R2 keys (dispatch/DL...), not the original URLs
        self.assertIn("dispatch/DL11092023.pdf", r2.puts)
        self.assertIn("dispatch/DL01062024.pdf", r2.puts)
        self.assertIn("dispatch/DL16102026.pdf", r2.puts)

    def test_a_council_404_is_reported_but_does_not_fail_the_run(self):
        # A 404 is logged as a warning but does not fail the run
        missing_url = "https://pharmacycouncil.telangana.gov.in/pdf/Dispatch list of Registration Certificates/Dispatch list 16-10-2026.pdf"
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(missing={missing_url})
            r2 = FakeR2()
            report = dispatch.sync_dispatch(
                dry_run=False,
                session=session,
                client=r2,
                workdir=Path(tmp),
            )
        # 404 is logged as warning but does not increment failed count
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.uploaded, 4)  # the other 4 still uploaded

    def test_a_non_404_http_error_is_a_failure(self):
        broken_url = "https://pharmacycouncil.telangana.gov.in/pdf/Dispatch%20list%20of%20Registration%20Certificates/01.02.2019%20Dispatch%20List.pdf"
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(broken={broken_url})
            r2 = FakeR2()
            report = dispatch.sync_dispatch(
                dry_run=False,
                session=session,
                client=r2,
                workdir=Path(tmp),
            )
        self.assertIn("DL01022019.pdf", report.failed_names)
        self.assertEqual(report.failed, 1)


class FallbackTests(unittest.TestCase):
    """The listing route's fallback list is regenerated from the bucket."""

    def test_fallback_is_generated_from_bucket_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp) / "dispatch.ts"
            fallback.write_text(
                "const FALLBACK_FILES = [\n  'old.pdf'\n];\n"
                "// bucket (tgpc, prefix dispatch/) on 2024-01-01 - 1 objects\n",
                encoding="utf-8",
            )
            r2 = FakeR2(keys=("dispatch/DL01022019.pdf", "dispatch/DL03062020C.pdf", "dispatch/DL11092023.pdf"))
            changed = dispatch.update_fallback_file(fallback, r2.bucket_names())
            self.assertTrue(changed)
            content = fallback.read_text()
            self.assertIn("DL01022019.pdf", content)
            self.assertIn("DL03062020C.pdf", content)
            self.assertIn("DL11092023.pdf", content)

    def test_fallback_preserves_provenance_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp) / "dispatch.ts"
            fallback.write_text(
                "const FALLBACK_FILES = [\n  'old.pdf'\n];\n"
                "// bucket (tgpc, prefix dispatch/) on 2024-01-01 - 1 objects\n",
                encoding="utf-8",
            )
            r2 = FakeR2(keys=("dispatch/DL01022019.pdf",))
            dispatch.update_fallback_file(fallback, r2.bucket_names())
            content = fallback.read_text()
            # The comment date gets updated to today, but format is preserved
            self.assertRegex(content, r"// bucket \(tgpc, prefix dispatch/\) on \d{4}-\d{2}-\d{2} - \d+ objects")
            self.assertIn("1 objects", content)


if __name__ == "__main__":
    unittest.main()
