"""Regression tests for security-audit run tgpc-run-1 leads 1 and 5-7.

Lead 1 (captcha-gated PII anon-readable): rph_dg_contacts holds mobile / email /
home address that TGPC gates behind a captcha at the source, so it must never
be anon- or authenticated-readable — no RLS policy, no grant, service_role only.
Lead 5 (FINGERPRINT-email-html-unescaped-names): scraped record fields must be
HTML-escaped before landing in the operator report email.
Lead 6 (FINGERPRINT-rclone-config-tmpfile): decoded rclone configs must use
unique, unpredictable temp paths — never fixed /tmp names.
Lead 7 (FINGERPRINT-ssrf-photo-redirect-follow): the photo fetch must
re-validate every redirect hop against the same-host policy. A refused hop is
logged and skipped (photo simply not downloaded), never fatal to the record.
"""

import base64
import json
import os
import re
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.modules["supabase"] = MagicMock()

from tgpc import quota
from tgpc.details_dg import (
    DG_COLUMNS,
    PUBLIC_R2_BUCKET,
    build_supabase_payload,
    push_dg_to_sb_storage,
    push_file_to_r2,
    upsert_dg_batch,
)
from tgpc.manager import Manager
from tgpc.scraper import PharmacistRecord, Scraper
from tgpc.utils import Config


def make_response(html: str) -> MagicMock:
    response = MagicMock()
    response.content = html.encode("utf-8")
    response.text = html
    return response


def noisy_image_bytes(size=(64, 64)) -> bytes:
    """A WEBP payload comfortably above the scraper's 100-byte minimum gate.

    A flat-colour image compresses below 100 bytes and would be dropped by the
    size filter before the photo pipeline ever runs, so use random noise.
    """
    from PIL import Image

    buf = BytesIO()
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buf, format="WEBP", quality=80)
    return buf.getvalue()


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


class EmailHtmlEscapingTests(unittest.TestCase):
    """Lead 5: record fields must be escaped in the report email HTML."""

    # Paren-free payload on purpose: sync_to_email strips a trailing
    # parenthetical from the display name, so parentheses would be truncated
    # before the escaping property under test is exercised.
    DETAILS = {
        "new_details": ['RPH001 - Evil <img src=x onerror="pwn"> (BPharm)'],
        "modified_details": [],
        "removed_details": [],
        "new_cat_stats": {"BPharm": 1},
        "rem_cat_stats": {},
        "mod_cat_stats": {},
        "total_records": 100,
    }

    @staticmethod
    def _send(manager, details):
        manager._last_update_details = details
        env = {"RESEND_API_KEY": "k", "NOTIFICATION_EMAIL": "to@example.com"}
        resp = MagicMock(ok=True, text='{"id":"abc"}')
        with patch("tgpc.manager.os.environ", env):
            with patch("tgpc.manager.requests.post", return_value=resp) as post:
                assert manager.sync_to_email() is True
        return post.call_args.kwargs["json"]["html"]

    def test_hostile_record_fields_are_escaped_in_html_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            html = self._send(manager, dict(self.DETAILS))
            # Raw markup from a scraped field must never survive into the body.
            # (The word "onerror" may appear as inert escaped text; the raw tag
            # and its attribute delimiter must not.)
            self.assertNotIn("<img", html)
            self.assertNotIn('onerror="', html)
            # The escaped text must still be present (entity or numeric form),
            # attribute quotes included — otherwise the payload could break out
            # of the surrounding markup.
            self.assertIn("&lt;img src=x onerror=&quot;pwn&quot;&gt;", html)

    def test_hostile_registration_numbers_are_escaped_too(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            details = dict(self.DETAILS)
            details["new_details"] = ["RPH009<b> - Name (BPharm)"]
            html = self._send(manager, details)
            self.assertNotIn("<b>", html)
            self.assertIn("&lt;b&gt;", html)

    def test_benign_names_still_render_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            details = dict(self.DETAILS)
            details["new_details"] = ["RPH002 - Devarakonda Lakshmi Narayana (BPharm)"]
            html = self._send(manager, details)
            self.assertIn("Devarakonda Lakshmi Narayana", html)


class RcloneTempFileTests(unittest.TestCase):
    """Lead 6: config must go to a unique temp file, not a fixed /tmp name."""

    @staticmethod
    def _env(config_b64):
        return {"RCLONE_GDRIVE_CONFIG": base64.b64encode(config_b64).decode()}

    def test_manager_uses_unique_temp_path_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = make_manager(temp_dir)
            manager.file_manager.save([record("RPH001")])
            observed = {}

            def fake_run(cmd, **kwargs):
                conf = Path(kwargs["env"]["RCLONE_CONFIG"])
                observed["path"] = conf
                observed["unique_name"] = conf.name != "rclone-gdrive.conf"
                observed["exists_during_run"] = conf.exists()
                observed["content"] = conf.read_bytes()
                return MagicMock(returncode=0)

            with patch("tgpc.manager.os.environ", self._env(b"rclone-config")):
                with patch("tgpc.manager.subprocess.run", side_effect=fake_run):
                    self.assertTrue(manager.sync_to_gdrive())

            self.assertTrue(observed["unique_name"], "must not use the fixed /tmp/rclone-gdrive.conf name")
            self.assertIn(str(Path(tempfile.gettempdir())), str(observed["path"]))
            self.assertTrue(observed["exists_during_run"])
            self.assertEqual(observed["content"], b"rclone-config")
            self.assertFalse(observed["path"].exists(), "config must be removed after the run")
            self.assertFalse(Path("/tmp/rclone-gdrive.conf").exists())

    def test_quota_uses_unique_temp_path_and_cleans_up(self):
        observed = {}

        def fake_run(cmd, **kwargs):
            conf = Path(cmd[cmd.index("--config") + 1])
            observed["path"] = conf
            observed["unique_name"] = conf.name != "rclone-quota.conf"
            observed["exists_during_run"] = conf.exists()
            observed["content"] = conf.read_bytes()
            return MagicMock(returncode=1, stdout=b"{}")

        with patch("tgpc.quota.os.environ", self._env(b"quota-config")):
            with patch("tgpc.quota.subprocess.run", side_effect=fake_run):
                quota.check_google_drive()

        self.assertTrue(observed["unique_name"], "must not use the fixed /tmp/rclone-quota.conf name")
        self.assertTrue(observed["exists_during_run"])
        self.assertEqual(observed["content"], b"quota-config")
        self.assertFalse(observed["path"].exists())
        self.assertFalse(Path("/tmp/rclone-quota.conf").exists())


class PhotoRedirectValidationTests(unittest.TestCase):
    """Lead 7: every redirect hop must pass the same-host policy.

    A refused hop logs a warning and skips the photo; the record itself still
    parses (the photo download is best-effort by design) — the security
    property under test is that no fetch ever reaches the disallowed host.
    """

    @staticmethod
    def _detail_page(src):
        return f"""
        <html><body>
        <table>
            <tr><th>Registration No</th><th>Name</th></tr>
            <tr><td>RPH777</td><td>Photo User</td></tr>
        </table>
        <img id="imgPhotoMain" src="{src}" />
        </body></html>
        """

    def _run(self, html, photo_responses, img_dir=None):
        """Run extract_detailed_info with the page + queued photo responses.

        Returns (record, photo_urls) where photo_urls lists each URL the
        photo fetch attempted (in order).
        """
        scraper = Scraper()
        seen = []

        def fake_request(method, url, **kwargs):
            if "getsearchpharmacist" in url:
                return make_response(html)
            seen.append((url, kwargs.get("allow_redirects")))
            if not photo_responses:
                raise AssertionError(f"unexpected extra photo fetch: {url}")
            return photo_responses.pop(0)

        with patch.object(scraper, "_request", side_effect=fake_request):
            result = scraper.extract_detailed_info("RPH777", Path(img_dir) if img_dir else None)
        return result, seen

    def test_cross_host_redirect_is_never_fetched(self):
        html = self._detail_page("https://www.pharmacycouncil.telangana.gov.in/photos/RPH777.jpg")
        redirect = MagicMock(status_code=302, headers={"Location": "https://evil.example.com/x.jpg"})

        with tempfile.TemporaryDirectory() as img_dir:
            result, seen = self._run(html, [redirect], img_dir)

        self.assertIsNotNone(result)
        # Only the first hop was attempted; the evil host was never touched.
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "https://www.pharmacycouncil.telangana.gov.in/photos/RPH777.jpg")
        self.assertIs(seen[0][1], False, "photo fetch must pass allow_redirects=False")
        self.assertEqual(list(Path(img_dir).glob("*.webp")), [], "refused photo must not be saved")

    def test_same_host_redirect_chain_is_followed_and_serves_bytes(self):
        """Legitimate same-host redirects must keep working end to end."""
        html = self._detail_page("https://www.pharmacycouncil.telangana.gov.in/photos/RPH777.jpg")
        redirect = MagicMock(status_code=302, headers={"Location": "/photos/moved/RPH777.jpg"})
        try:
            payload = noisy_image_bytes()
        except ImportError:  # pragma: no cover
            self.skipTest("Pillow not installed")
        final = MagicMock(status_code=200, content=payload)

        with tempfile.TemporaryDirectory() as img_dir:
            result, seen = self._run(html, [redirect, final], img_dir)

            self.assertIsNotNone(result)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[1][0], "https://www.pharmacycouncil.telangana.gov.in/photos/moved/RPH777.jpg")
            self.assertIs(seen[1][1], False)
            self.assertTrue(
                Path(img_dir, "RPH777.webp").exists(),
                "a same-host redirect must still deliver the photo",
            )

    def test_traversal_in_redirect_is_never_fetched(self):
        html = self._detail_page("https://www.pharmacycouncil.telangana.gov.in/photos/RPH777.jpg")
        redirect = MagicMock(status_code=302, headers={"Location": "/photos/../../secret/RPH777.jpg"})

        with tempfile.TemporaryDirectory() as img_dir:
            result, seen = self._run(html, [redirect], img_dir)

        self.assertIsNotNone(result)
        self.assertEqual(len(seen), 1, "traversal hop must not be followed")

    def test_plain_200_photo_still_downloads(self):
        """No behavior change for the normal case: direct 200 photo downloads."""
        html = self._detail_page("https://www.pharmacycouncil.telangana.gov.in/photos/RPH777.jpg")
        try:
            payload = noisy_image_bytes()
        except ImportError:  # pragma: no cover
            self.skipTest("Pillow not installed")
        direct = MagicMock(status_code=200, content=payload)

        with tempfile.TemporaryDirectory() as img_dir:
            result, seen = self._run(html, [direct], img_dir)

            self.assertIsNotNone(result)
            self.assertEqual(len(seen), 1)
            self.assertTrue(Path(img_dir, "RPH777.webp").exists(), "normal photo flow must still save the webp")


REPO_ROOT = Path(__file__).resolve().parents[1]
PII_TABLE = "rph_dg_contacts"

PUBLIC_ROLES = {"anon", "authenticated", "public"}
# A GRANT's role list runs to the end of the statement; a CREATE POLICY's runs
# until the next policy clause. This captures both without swallowing the
# `USING (true)` body — the exact shape of the flaw being guarded against.
ROLE_LIST = re.compile(r"\bTO\s+([A-Za-z_\s\"]+?)(?=\s+(?:USING|WITH|FOR|AS)\b|$)", re.I | re.S)


def sql_files():
    """Every *.sql in the repo — the only place a grant may legitimately live."""
    return sorted(REPO_ROOT.rglob("*.sql"))


def strip_sql_comments(sql: str) -> str:
    """Drop -- and /* */ comments so commented-out SQL cannot hide a grant."""
    return re.sub(r"--[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", sql, flags=re.S))


def statements(sql: str):
    return [s.strip() for s in strip_sql_comments(sql).split(";") if s.strip()]


def public_roles_granted(statement: str):
    """Roles in the statement's TO clause that reach the browser / any user."""
    match = ROLE_LIST.search(statement)
    if not match:
        return []
    roles = {r.strip().strip('"').lower() for r in match.group(1).split(",")}
    return sorted(roles & PUBLIC_ROLES)


class DgPiiLockdownTests(unittest.TestCase):
    """Lead 1: captcha-gated DG PII must never be readable by a public role.

    The live Supabase project is out of reach from a unit test, so these guard
    the migration that defines the posture — the exact place the flaw came
    from — and fail if any SQL in the repo re-grants the table.
    """

    MIGRATION = REPO_ROOT / "tgpc" / "dg_migration.sql"

    @classmethod
    def setUpClass(cls):
        cls.migration = cls.MIGRATION.read_text(encoding="utf-8")

    def test_the_repo_wide_scan_actually_sees_the_migration(self):
        found = sql_files()
        self.assertTrue(found, "no .sql files discovered — the guard would pass vacuously")
        self.assertIn(self.MIGRATION, found)

    def test_migration_declares_the_pii_table_and_its_columns(self):
        self.assertIn(f"CREATE TABLE IF NOT EXISTS public.{PII_TABLE}", self.migration)
        for column in ("mobile_no", "email_id", "home_address", "dob"):
            self.assertRegex(self.migration, rf"\b{column}\s+TEXT\b")

    def test_rls_is_enabled_on_the_pii_table(self):
        self.assertIn(f"ALTER TABLE public.{PII_TABLE} ENABLE ROW LEVEL SECURITY", self.migration)

    def test_migration_revokes_every_public_role_and_keeps_service_role(self):
        body = strip_sql_comments(self.migration)
        # Table-level, so it covers columns added to the pipeline later too.
        for role in ("anon", "authenticated"):
            self.assertRegex(
                body,
                rf"REVOKE\s+ALL\s+ON\s+TABLE\s+public\.{PII_TABLE}\s+FROM\s+[^;]*\b{role}\b",
                f"{role} must hold no privilege on the captcha-gated PII table",
            )
        self.assertRegex(
            body,
            rf"GRANT\s+[^;]*\bON\s+TABLE\s+public\.{PII_TABLE}\s+TO\s+[^;]*\bservice_role\b",
            "the enrichment pipeline reads this table with the service key",
        )

    def test_no_sql_anywhere_grants_the_pii_table_to_a_public_role(self):
        """Any future migration that re-opens the table (a permissive policy or
        a GRANT to anon/authenticated/PUBLIC) fails here."""
        offenders = []
        for path in sql_files():
            for statement in statements(path.read_text(encoding="utf-8")):
                if PII_TABLE not in statement:
                    continue
                if not re.search(r"\b(?:GRANT|CREATE\s+POLICY)\b", statement, re.I):
                    continue
                roles = public_roles_granted(statement)
                if roles:
                    offenders.append(f"{path.name}: {roles} in `{statement[:100]}`")
        self.assertEqual(offenders, [], "captcha-gated PII granted to a public role")

    def test_migration_covers_every_column_the_pipeline_writes(self):
        """Drift guard: a new DG_COLUMNS entry must exist in the DDL, so the
        table-level REVOKE keeps protecting it."""
        for column in DG_COLUMNS:
            self.assertRegex(
                self.migration,
                rf"\b{re.escape(column)}\b\s+TEXT",
                f"{column} is written by the pipeline but missing from the DDL",
            )

    def test_pipeline_upsert_needs_the_service_key_not_the_browser_key(self):
        """The pipeline itself must be unable to reach the table with the
        publishable/anon key that ships to every browser."""
        payload = [build_supabase_payload({"registration_number": "RPH001"}, "2026-01-01T00:00:00Z")]
        env = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "anon-key"}
        with patch.dict(os.environ, env, clear=True):
            count, error = upsert_dg_batch(payload)
        self.assertEqual(count, 0)
        self.assertIn("SECRET_KEY", error)


def _r2_env(bucket):
    env = {
        "CLOUDFLARE_ACCOUNT_ID": "acct",
        "R2_ACCESS_KEY_ID": "ak",
        "R2_SECRET_ACCESS_KEY": "sk",
    }
    if bucket is not None:
        env["TGPC_R2_DG_BUCKET"] = bucket
    return env


class DgR2BucketIsolationTests(unittest.TestCase):
    """Lead 2: DG PII must never be written to the anonymously-readable bucket.

    `pub-...r2.dev` serves every key in the public bucket to unauthenticated
    callers (verified: known objects 200, absent keys 404), and the pipeline
    used to write `dg-raw/{REG}.json` + `dg-contacts/dg_contacts.jsonl` there.
    """

    DETAILS_DG = REPO_ROOT / "tgpc" / "details_dg.py"

    def _push(self, bucket, key="dg-raw/TS003261.json"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TS003261.json"
            path.write_text('{"registration_number": "TS003261"}', encoding="utf-8")
            with patch.dict(os.environ, _r2_env(bucket), clear=True):
                with patch("boto3.client") as client:
                    return push_file_to_r2(path, key), client

    def test_no_dg_write_targets_the_public_bucket(self):
        source = self.DETAILS_DG.read_text(encoding="utf-8")
        # Exactly one put_object, and it uses the guarded bucket variable — a
        # second, unguarded R2 write cannot be added without failing here.
        self.assertEqual(
            source.count("put_object("),
            source.count("put_object(Bucket=bucket"),
            "every DG -> R2 write must go through the guarded put_object call",
        )
        self.assertIn("bucket == PUBLIC_R2_BUCKET", source)
        for literal in ('Bucket="tgpc"', "Bucket='tgpc'", 'Bucket="tgpc-'):
            self.assertNotIn(literal, source)

    def test_push_refuses_when_the_private_bucket_is_unset(self):
        pushed, client = self._push(None)
        self.assertFalse(pushed, "an unset bucket must refuse, never fall back to the public one")
        client.assert_not_called()

    def test_push_refuses_when_pointed_at_the_public_bucket(self):
        pushed, client = self._push(PUBLIC_R2_BUCKET)
        self.assertFalse(pushed, "pointing the DG bucket at the public bucket must refuse")
        client.assert_not_called()

    def test_push_uses_the_configured_private_bucket(self):
        pushed, client = self._push("tgpc-dg-private")
        self.assertTrue(pushed)
        self.assertEqual(client.return_value.put_object.call_args.kwargs["Bucket"], "tgpc-dg-private")


def backup_payload(count):
    return json.dumps([{"registration_number": f"RPH{i:04d}", "name": f"Name {i}"} for i in range(count)])


def _restore_env():
    return {
        "CLOUDFLARE_ACCOUNT_ID": "acct",
        "R2_ACCESS_KEY_ID": "ak",
        "R2_SECRET_ACCESS_KEY": "sk",
        "TGPC_ALLOW_SMALL_RPH": "1",
    }


class RestoreValidationTests(unittest.TestCase):
    """Lead 8: a restore must not trust the newest object, nor a bare count.

    The local file is deliberately left invalid so the R2 path is exercised;
    after any failure it must still be byte-identical to what it started as.
    """

    def _run_restore(self, temp_dir, contents, payloads):
        """Restore against mocked R2. Returns (result, keys fetched, in order)."""
        manager = make_manager(temp_dir)
        manager.file_manager.save([])  # invalid local file -> restore path
        fetched = []

        def fake_get(key, dest):
            fetched.append(key)
            if key not in payloads:
                raise AssertionError(f"unexpected fetch of {key}")
            Path(dest).write_text(payloads[key], encoding="utf-8")
            return True

        with patch("tgpc.manager.os.environ", _restore_env()):
            with patch("tgpc.manager.R2Client") as r2_cls:
                client = MagicMock()
                client.list_objects.return_value = contents
                client.get_object.side_effect = fake_get
                r2_cls.return_value = client
                result = manager._restore_rph_from_backup()
        return result, fetched

    @staticmethod
    def _local(temp_dir):
        return json.loads((Path(temp_dir) / "rph.json").read_text())

    def test_newest_valid_backup_is_used(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payloads = {
                "backups/old.json": backup_payload(2),
                "backups/new.json": backup_payload(3),
            }
            contents = [
                {"Key": "backups/old.json", "LastModified": "2020-01-01", "Size": 5000},
                {"Key": "backups/new.json", "LastModified": "2026-01-01", "Size": 5000},
            ]
            result, fetched = self._run_restore(temp_dir, contents, payloads)
            self.assertTrue(result)
            self.assertEqual(fetched, ["backups/new.json"], "must try the newest first")
            self.assertEqual(len(self._local(temp_dir)), 3)

    def test_falls_back_to_an_older_backup_when_the_newest_is_unusable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payloads = {
                "backups/old.json": backup_payload(2),
                "backups/new.json": "<html>502 gateway timeout</html>",  # truncated upload
            }
            contents = [
                {"Key": "backups/old.json", "LastModified": "2020-01-01", "Size": 5000},
                {"Key": "backups/new.json", "LastModified": "2026-01-01", "Size": 5000},
            ]
            result, fetched = self._run_restore(temp_dir, contents, payloads)
            self.assertTrue(result, "one broken newest backup must not abort the whole update")
            self.assertEqual(fetched, ["backups/new.json", "backups/old.json"])
            self.assertEqual(len(self._local(temp_dir)), 2)

    def test_count_passing_but_wrong_shaped_backup_is_rejected(self):
        """Clearing the record-count floor is not proof of a usable registry."""
        with tempfile.TemporaryDirectory() as temp_dir:
            junk = json.dumps([{"registration_number": ""}, None, {"name": "no reg at all"}])
            contents = [{"Key": "backups/new.json", "LastModified": "2026-01-01", "Size": 5000}]
            result, _ = self._run_restore(temp_dir, contents, {"backups/new.json": junk})
            self.assertFalse(result)
            self.assertEqual(self._local(temp_dir), [], "local registry must be left untouched")
            self.assertFalse((Path(temp_dir) / "rph.restore_tmp").exists(), "no temp file may be left behind")

    def test_objects_that_are_not_json_backups_are_never_downloaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            payloads = {"backups/old.json": backup_payload(2)}
            contents = [
                {"Key": "backups/old.json", "LastModified": "2020-01-01", "Size": 5000},
                {"Key": "backups/rph_backup_20260101.pdf", "LastModified": "2026-01-01", "Size": 9000},
            ]
            result, fetched = self._run_restore(temp_dir, contents, payloads)
            self.assertTrue(result)
            self.assertEqual(fetched, ["backups/old.json"], "a newer non-.json object must be skipped")


class DgStorageDestinationTests(unittest.TestCase):
    """Lead 2: the Supabase Storage copy carries the same PII as the R2 one.

    Its exposure depends on a bucket flag that lives in the dashboard, so the
    push verifies it rather than assuming — and refuses anything that is not
    provably private, including "could not check".
    """

    OBJECT = "dg_contacts.jsonl"

    @staticmethod
    def _bucket_response(public, ok=True, status=200):
        return MagicMock(ok=ok, status_code=status, json=lambda: {} if public is None else {"public": public})

    def _run_push(self, get_mock, post_mock):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / self.OBJECT
            path.write_text('{"registration_number": "TS003261"}', encoding="utf-8")
            env = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SECRET_KEY": "service-key"}
            with patch.dict(os.environ, env, clear=True):
                with patch("requests.get", get_mock):
                    with patch("requests.post", post_mock):
                        return push_dg_to_sb_storage(path, self.OBJECT)

    def test_refuses_when_the_storage_bucket_is_public(self):
        post = MagicMock()
        pushed = self._run_push(MagicMock(return_value=self._bucket_response(True)), post)
        self.assertFalse(pushed)
        post.assert_not_called()

    def test_refuses_when_the_bucket_cannot_be_verified(self):
        cases = {
            "transport error": MagicMock(side_effect=RuntimeError("boom")),
            "api error": MagicMock(return_value=self._bucket_response(None, ok=False, status=500)),
            "no public flag in the payload": MagicMock(return_value=self._bucket_response(None)),
        }
        for label, get_mock in cases.items():
            post = MagicMock()
            with self.subTest(label):
                self.assertFalse(self._run_push(get_mock, post), f"{label} must fail closed")
                post.assert_not_called()

    def test_uploads_only_when_the_bucket_is_private(self):
        post = MagicMock(return_value=MagicMock(ok=True, status_code=200))
        pushed = self._run_push(MagicMock(return_value=self._bucket_response(False)), post)
        self.assertTrue(pushed)
        post.assert_called_once()
        self.assertIn("/storage/v1/object/tgpc/dg_contacts.jsonl", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["x-upsert"], "true")


if __name__ == "__main__":
    unittest.main()
