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
from datetime import date
from pathlib import Path
from unittest.mock import patch

import requests

import tgpc.dispatch as dispatch
from tgpc.storage import R2Client

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

    def get_object(self, key, path):
        """Write a stand-in body of the recorded size and return the path."""
        Path(path).write_bytes(b"x" * self.objects.get(key, 0))
        return path

    def put_object(self, key, path):
        self.objects[key] = path.stat().st_size
        if key not in self.keys:
            self.keys.append(key)
        self.puts.append(key)
        return True

    def head_object(self, key):
        size = self.objects.get(key, -1)
        return {"ContentLength": 0 if self.bad_head else size}


def pdf_blob(size=2000):
    return b"%PDF-1.7\n" + b"0" * size


class DispatchNameTests(unittest.TestCase):
    def test_maps_every_naming_scheme_the_council_has_used(self):
        cases = {
            "01.02.2019 Dispatch List.pdf": "DL01022019.pdf",
            "03.06.2020 Dispatch List c.pdf": "DL03062020C.pdf",
            "03.06.2020 Dispatch List d.pdf": "DL03062020D.pdf",
            "11-09-2023 Dispatch List.pdf": "DL11092023.pdf",
            "Dispatch list 16-10-2026.pdf": "DL16102026.pdf",
            "Dispatch list 01-6-2024.pdf": "DL01062024.pdf",
            "Dispatch list of Registration Certificates 05-09-2026.pdf": "DL05092026.pdf",
            "dispatch list 9-9-2025.pdf": "DL09092025.pdf",
        }
        for filename, expected in cases.items():
            self.assertEqual(dispatch.dispatch_name(filename), expected, filename)

    def test_rejects_impossible_dates_and_unrelated_files(self):
        for filename in (
            "31.02.2020 Dispatch List.pdf",
            "00.01.2020 Dispatch List.pdf",
            "35.12.2020 Dispatch List.pdf",
            "Guidelines.pdf",
            "THIRD FLOOR DISPATCH LIST.pdf",
            "Dispatch list 2026.pdf",
            "",
        ):
            self.assertIsNone(dispatch.dispatch_name(filename), filename)

    def test_suffix_is_upper_cased(self):
        self.assertEqual(dispatch.dispatch_name("03.06.2020 Dispatch List C.pdf"), "DL03062020C.pdf")


class ParsePageTests(unittest.TestCase):
    def test_parses_dedupes_and_sorts_by_date(self):
        entries, skipped = dispatch.parse_dispatch_page(PAGE_TEMPLATE)

        self.assertEqual(
            [e.name for e in entries],
            [
                "DL01022019.pdf",
                "DL03062020C.pdf",
                "DL11092023.pdf",
                "DL01062024.pdf",
                "DL16102026.pdf",
            ],
        )
        # One date is linked twice; the first name wins.
        self.assertEqual(len([e for e in entries if e.name == "DL11092023.pdf"]), 1)
        self.assertEqual(entries[-1].date, date(2026, 10, 16))
        # Guidelines.pdf is not a dispatch list, so it is not "skipped" either.
        self.assertEqual(skipped, ["THIRD FLOOR DISPATCH LIST.pdf"])

    def test_urls_are_absolute_and_stripped_of_the_session_id(self):
        entries, _ = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        urls = {e.name: e.url for e in entries}
        self.assertEqual(
            urls["DL01022019.pdf"],
            dispatch.SOURCE_HOST
            + "/pdf/Dispatch%20list%20of%20Registration%20Certificates/01.02.2019%20Dispatch%20List.pdf",
        )
        self.assertNotIn("jsessionid", urls["DL01022019.pdf"])
        # Literal spaces are what the council publishes (requests encodes them);
        # the session id must never survive into the download URL.
        self.assertNotIn("jsessionid", urls["DL03062020C.pdf"])
        self.assertTrue(urls["DL03062020C.pdf"].startswith(dispatch.SOURCE_HOST + "/pdf/"))

    def test_empty_page_yields_no_entries(self):
        self.assertEqual(dispatch.parse_dispatch_page("<html><body>No App's are avail..</body></html>"), ([], []))

    def test_workspace_relative_names_are_not_mistaken_for_links(self):
        html = '<a href="/pdf/elsewhere/01.02.2019 Dispatch List.pdf?x=1;jsessionid=1">Click here</a>'
        entries, skipped = dispatch.parse_dispatch_page(html)
        self.assertEqual([e.name for e in entries], ["DL01022019.pdf"])
        self.assertEqual(skipped, [])


class FallbackListTests(unittest.TestCase):
    ROUTE = """// Last-known dispatch PDFs.
// bucket (tgpc, prefix dispatch/) on 2026-10-22 - 220 objects. Regenerate
// with the R2 S3 credentials rather than editing by hand.
const FALLBACK_FILES = [
  'DL01022019.pdf', 'DL03062020C.pdf'
];
"""

    def test_renders_four_names_per_line_without_a_trailing_comma(self):
        block = dispatch.render_fallback([f"DL0{i}022019.pdf" for i in range(1, 6)])
        self.assertEqual(
            block,
            "const FALLBACK_FILES = [\n"
            "  'DL01022019.pdf', 'DL02022019.pdf', 'DL03022019.pdf', 'DL04022019.pdf',\n"
            "  'DL05022019.pdf'\n"
            "];",
        )

    def test_rewrites_the_block_and_the_provenance_comment(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        route = tmp / "dispatch.ts"
        route.write_text(self.ROUTE, encoding="utf-8")

        changed = dispatch.update_fallback_file(route, {"DL16102026.pdf", "DL01022019.pdf"}, today=date(2026, 11, 2))

        self.assertTrue(changed)
        text = route.read_text(encoding="utf-8")
        self.assertIn("prefix dispatch/) on 2026-11-02 - 2 objects", text)
        self.assertIn("  'DL01022019.pdf', 'DL16102026.pdf'", text)
        self.assertNotIn("DL03062020C.pdf", text)
        self.assertIn("const FALLBACK_FILES = [\n", text)

        # Idempotent: the second run has nothing to write.
        self.assertFalse(
            dispatch.update_fallback_file(route, {"DL16102026.pdf", "DL01022019.pdf"}, today=date(2026, 11, 2))
        )

    def test_refuses_a_route_without_the_generated_block(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        route = tmp / "dispatch.ts"
        route.write_text("export const x = 1;\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            dispatch.update_fallback_file(route, ["DL01022019.pdf"])


class PlanTests(unittest.TestCase):
    def entries(self):
        entries, _ = dispatch.parse_dispatch_page(PAGE_TEMPLATE)
        return entries

    def test_only_missing_files_are_downloaded(self):
        syncer = dispatch.DispatchSyncer(client=FakeR2())
        present = {"DL01022019.pdf", "DL11092023.pdf", "DL99999999.pdf"}
        to_upload, extra = syncer.plan(self.entries(), present)
        self.assertEqual([e.name for e in to_upload], ["DL03062020C.pdf", "DL01062024.pdf", "DL16102026.pdf"])
        # Files in the bucket that the council no longer links are reported,
        # never deleted.
        self.assertEqual(extra, ["DL99999999.pdf"])

    def test_force_reuploads_everything(self):
        syncer = dispatch.DispatchSyncer(client=FakeR2())
        to_upload, extra = syncer.plan(self.entries(), {e.name for e in self.entries()}, force=True)
        self.assertEqual(len(to_upload), 5)
        self.assertEqual(extra, [])


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.entry = dispatch.DispatchPdf(
            name="DL16102026.pdf",
            filename="Dispatch list 16-10-2026.pdf",
            url=dispatch.SOURCE_HOST + "/pdf/x/Dispatch%20list%2016-10-2026.pdf",
            date=date(2026, 10, 16),
        )

    def test_download_writes_the_pdf_to_the_expected_name(self):
        syncer = dispatch.DispatchSyncer(client=FakeR2(), session=FakeSession())
        path = syncer.download(self.entry, self.tmp)
        self.assertEqual(path.name, "DL16102026.pdf")
        self.assertEqual(path.stat().st_size, len(pdf_blob()))

    def test_download_rejects_an_error_page(self):
        session = FakeSession(pdf_bytes=b"<html>500</html>")
        syncer = dispatch.DispatchSyncer(client=FakeR2(), session=session)
        with self.assertRaises(ValueError):
            syncer.download(self.entry, self.tmp)
        # The unusable body is removed, so a later run cannot mistake it for data.
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_upload_fails_loudly_when_the_stored_size_disagrees(self):
        syncer = dispatch.DispatchSyncer(client=FakeR2(bad_head=True), session=FakeSession())
        path = self.tmp / self.entry.name
        path.write_bytes(pdf_blob())
        with self.assertRaises(ValueError):
            syncer.upload(self.entry, path)

    def test_upload_stores_under_the_dispatch_prefix(self):
        client = FakeR2()
        syncer = dispatch.DispatchSyncer(client=client, session=FakeSession())
        path = self.tmp / self.entry.name
        path.write_bytes(pdf_blob())
        syncer.upload(self.entry, path)
        self.assertEqual(client.puts, ["dispatch/DL16102026.pdf"])


class BucketListingTests(unittest.TestCase):
    def test_bucket_names_strips_the_prefix_and_ignores_nested_keys(self):
        client = FakeR2(
            keys=["dispatch/DL01022019.pdf", "dispatch/DL16102026.pdf", "dispatch/nested/x.pdf", "photos/a.webp"]
        )
        syncer = dispatch.DispatchSyncer(client=client)
        self.assertEqual(syncer.bucket_names(), {"DL01022019.pdf", "DL16102026.pdf"})


class SyncRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_dry_run_reports_without_writing_anything(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        session = FakeSession()
        report = dispatch.sync_dispatch(dry_run=True, client=client, session=session, workdir=self.tmp)

        self.assertEqual(report.published, 5)
        self.assertEqual(report.already_present, 1)
        self.assertEqual(report.missing, 4)
        self.assertEqual(report.newest, "DL16102026.pdf")
        self.assertEqual(report.extra_in_bucket, [])
        self.assertEqual(client.puts, [])
        # Only the report page is fetched; no PDF is requested.
        self.assertEqual(session.requested, [dispatch.DISPATCH_PAGE])

    def test_uploads_only_the_missing_files_and_cleans_up(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        report = dispatch.sync_dispatch(client=client, session=FakeSession(), workdir=self.tmp)

        self.assertEqual(report.uploaded, 4)
        self.assertEqual(report.failed, 0)
        self.assertEqual(
            sorted(client.puts),
            [
                "dispatch/DL01062024.pdf",
                "dispatch/DL03062020C.pdf",
                "dispatch/DL11092023.pdf",
                "dispatch/DL16102026.pdf",
            ],
        )
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_one_broken_pdf_does_not_stop_the_batch(self):
        broken = dispatch.SOURCE_HOST + "/pdf/Dispatch list of Registration Certificates/Dispatch list 16-10-2026.pdf"
        client = FakeR2()
        report = dispatch.sync_dispatch(client=client, session=FakeSession(broken=[broken]), workdir=self.tmp)

        self.assertEqual(report.uploaded, 4)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.failed_names, ["DL16102026.pdf"])
        self.assertNotIn("dispatch/DL16102026.pdf", client.puts)

    def test_a_council_404_is_reported_but_does_not_fail_the_run(self):
        # Seven links on the live page point at files the council does not host.
        # Retrying cannot fix that, so it is reported and the run stays green.
        missing = dispatch.SOURCE_HOST + "/pdf/Dispatch list of Registration Certificates/Dispatch list 16-10-2026.pdf"
        client = FakeR2()
        report = dispatch.sync_dispatch(client=client, session=FakeSession(missing=[missing]), workdir=self.tmp)

        self.assertEqual(report.uploaded, 4)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.unavailable_names, ["DL16102026.pdf"])
        self.assertNotIn("dispatch/DL16102026.pdf", client.puts)

    def test_limit_caps_uploads_per_run(self):
        client = FakeR2()
        report = dispatch.sync_dispatch(client=client, session=FakeSession(), workdir=self.tmp, limit=2)
        self.assertEqual(report.uploaded, 2)
        self.assertEqual(report.missing, 5)

    def test_empty_source_page_is_an_error_not_a_silent_no_op(self):
        client = FakeR2()
        with self.assertRaises(RuntimeError):
            dispatch.sync_dispatch(client=client, session=FakeSession(page_html="<html></html>"), workdir=self.tmp)

    def test_write_fallback_refreshes_the_route_from_the_bucket(self):
        route = self.tmp / "dispatch.ts"
        route.write_text(FallbackListTests.ROUTE, encoding="utf-8")
        from unittest.mock import patch

        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        with patch.object(dispatch, "FALLBACK_TS", route):
            dispatch.sync_dispatch(client=client, session=FakeSession(), workdir=self.tmp, write_fallback=True)

        text = route.read_text(encoding="utf-8")
        self.assertIn("DL16102026.pdf", text)
        self.assertIn(f"{len(client.keys)} objects", text)

    def test_missing_credentials_name_the_environment_variables(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                dispatch.r2_client_from_env()
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", str(ctx.exception))


class _Proc:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


class MirrorToDriveTests(unittest.TestCase):
    """R2 -> Drive: the copy that has to survive an R2 accident."""

    NAMES = ["DL01022019.pdf", "DL16102026.pdf"]

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.mirror = self.tmp / "mirror"

    def client(self):
        return FakeR2(keys=[f"dispatch/{n}" for n in self.NAMES])

    def test_downloads_every_object_and_copies_to_drive(self):
        client = self.client()
        calls = []

        def fake_rclone(args, timeout=1800):
            calls.append(args)
            return _Proc()

        with patch.object(dispatch, "_rclone", fake_rclone):
            report = dispatch.mirror_to_drive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.objects, 2)
        self.assertEqual(report.downloaded, 2)
        self.assertEqual(report.rclone, "ok")
        self.assertEqual(sorted(p.name for p in self.mirror.iterdir()), self.NAMES)
        # --size-only: R2 and Drive timestamps never match, so a time check
        # would re-download the entire archive on every run.
        self.assertEqual(calls, [["copy", "--size-only", str(self.mirror), "gdrive:x"]])

    def test_skips_files_already_at_the_right_size(self):
        client = self.client()
        self.mirror.mkdir(parents=True)
        (self.mirror / "DL01022019.pdf").write_bytes(b"x" * len(pdf_blob()))

        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc()):
            report = dispatch.mirror_to_drive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.objects, 2)
        self.assertEqual(report.downloaded, 1)
        self.assertEqual((self.mirror / "DL01022019.pdf").read_bytes(), b"x" * len(pdf_blob()))

    def test_a_truncated_local_copy_is_re_downloaded(self):
        client = self.client()
        self.mirror.mkdir(parents=True)
        (self.mirror / "DL16102026.pdf").write_bytes(b"short")

        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc()):
            report = dispatch.mirror_to_drive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.downloaded, 2)

    def test_missing_rclone_is_reported_not_raised(self):
        with patch.object(dispatch, "_rclone", lambda *a, **k: None):
            report = dispatch.mirror_to_drive(client=self.client(), mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.rclone, "missing")
        self.assertIn("rclone", report.error)
        # The local mirror is still complete, so a later run can finish the copy.
        self.assertEqual(len(list(self.mirror.iterdir())), 2)

    def test_failed_rclone_is_distinguishable_from_missing(self):
        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc(1, stderr="drive quota exceeded\n")):
            report = dispatch.mirror_to_drive(client=self.client(), mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.rclone, "failed")
        self.assertIn("quota", report.error)


class VerifyArchiveTests(unittest.TestCase):
    """The alarm: archived material must never disappear unnoticed."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.mirror = self.tmp / "mirror"
        self.mirror.mkdir()

    def write(self, name, size=len(pdf_blob())):
        (self.mirror / name).write_bytes(b"x" * size)

    def test_agreement_is_clean(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        self.write("DL01022019.pdf")

        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc()):
            report = dispatch.verify_archive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual((report.bucket, report.mirror), (1, 1))
        self.assertEqual(report.drive, "ok")
        self.assertEqual(report.problems, [])

    def test_bucket_object_missing_from_the_mirror_is_a_problem(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf", "dispatch/DL16102026.pdf"])
        self.write("DL01022019.pdf")

        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc()):
            report = dispatch.verify_archive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.missing_from_mirror, ["DL16102026.pdf"])
        self.assertIn("missing from the local mirror: DL16102026.pdf", report.problems)

    def test_a_file_gone_from_the_bucket_is_the_real_alarm(self):
        # Someone deleted an archived PDF from R2 but the mirror still has it.
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        self.write("DL01022019.pdf")
        self.write("DL16102026.pdf")

        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc()):
            report = dispatch.verify_archive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.gone_from_bucket, ["DL16102026.pdf"])
        self.assertIn("in the local mirror but gone from the bucket: DL16102026.pdf", report.problems)

    def test_size_mismatch_is_reported(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        self.write("DL01022019.pdf", size=7)

        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc()):
            report = dispatch.verify_archive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.size_mismatch, ["DL01022019.pdf"])
        self.assertIn("size mismatch: DL01022019.pdf", report.problems)

    def test_drive_drift_fails_the_check(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        self.write("DL01022019.pdf")

        with patch.object(dispatch, "_rclone", lambda *a, **k: _Proc(1, stderr="1 file differs\n")):
            report = dispatch.verify_archive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.drive, "drift")
        self.assertTrue(any("Drive backup out of sync" in p for p in report.problems))

    def test_missing_rclone_does_not_fail_the_bucket_check(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        self.write("DL01022019.pdf")

        with patch.object(dispatch, "_rclone", lambda *a, **k: None):
            report = dispatch.verify_archive(client=client, mirror_dir=self.mirror, remote="gdrive:x")

        self.assertEqual(report.drive, "skipped")
        self.assertEqual(report.problems, [])

    def test_check_drive_can_be_skipped_entirely(self):
        client = FakeR2(keys=["dispatch/DL01022019.pdf"])
        self.write("DL01022019.pdf")

        with patch.object(dispatch, "_rclone", side_effect=AssertionError("must not run")):
            report = dispatch.verify_archive(
                client=client, mirror_dir=self.mirror, remote="gdrive:x", check_drive=False
            )

        self.assertEqual(report.drive, "skipped")
        self.assertEqual(report.problems, [])

    def test_a_missing_mirror_directory_is_empty_not_an_error(self):
        client = FakeR2(keys=[])
        report = dispatch.verify_archive(
            client=client, mirror_dir=self.tmp / "nope", remote="gdrive:x", check_drive=False
        )
        self.assertEqual((report.bucket, report.mirror), (0, 0))
        self.assertEqual(report.problems, [])


class RcloneConfigTests(unittest.TestCase):
    """CI ships the Drive remote as base64; rclone only reads RCLONE_CONFIG."""

    def test_no_secret_means_no_override(self):
        with patch.dict("os.environ", {}, clear=True):
            with dispatch._rclone_config() as env:
                self.assertEqual(env, {})

    def test_secret_is_decoded_to_a_private_temp_file_that_is_removed(self):
        import base64

        blob = base64.b64encode(b"[gdrive]\ntype = drive\n").decode()
        with patch.dict("os.environ", {"RCLONE_GDRIVE_CONFIG": blob}, clear=True):
            with dispatch._rclone_config() as env:
                path = Path(env["RCLONE_CONFIG"])
                self.assertEqual(path.read_text(), "[gdrive]\ntype = drive\n")
                # Private: a decoded service-account config must not be readable
                # by other users while it exists.
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            self.assertFalse(path.exists())

    def test_existing_rclone_config_wins(self):
        with patch.dict("os.environ", {"RCLONE_GDRIVE_CONFIG": "aGk=", "RCLONE_CONFIG": "/tmp/mine.conf"}, clear=True):
            with dispatch._rclone_config() as env:
                self.assertEqual(env, {})


class R2PaginationTests(unittest.TestCase):
    def test_list_objects_all_follows_continuation_tokens(self):
        class FakeBoto:
            def __init__(self):
                self.calls = []

            def list_objects_v2(self, **kwargs):
                self.calls.append(kwargs)
                if "ContinuationToken" not in kwargs:
                    return {
                        "Contents": [{"Key": "dispatch/a.pdf"}],
                        "IsTruncated": True,
                        "NextContinuationToken": "t1",
                    }
                return {"Contents": [{"Key": "dispatch/b.pdf"}], "IsTruncated": False}

        client = R2Client.__new__(R2Client)
        client.client = FakeBoto()
        keys = [obj["Key"] for obj in client.list_objects_all("dispatch/")]

        self.assertEqual(keys, ["dispatch/a.pdf", "dispatch/b.pdf"])
        self.assertEqual(len(client.client.calls), 2)
        self.assertEqual(client.client.calls[1]["ContinuationToken"], "t1")


if __name__ == "__main__":
    unittest.main()
