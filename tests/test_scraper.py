import os
import ssl
import sys
import unittest
from unittest.mock import MagicMock, call, patch
from urllib.parse import urlparse

# Mock supabase before importing tgpc package modules
sys.modules["supabase"] = MagicMock()

from tgpc.scraper import Scraper, _is_blocked, _is_tgpc_host, _TGPCTLSAdapter, BLOCKED_MARKERS
from tgpc.utils import BlockedError, Config


def make_response(html: str) -> MagicMock:
    response = MagicMock()
    response.content = html.encode("utf-8")
    response.text = html
    return response


class ScraperParsingTests(unittest.TestCase):
    def test_request_uses_split_connect_and_read_timeouts(self):
        scraper = Scraper()
        response = MagicMock()
        response.status_code = 200
        response.text = "ok" * 1000
        response.raise_for_status.return_value = None

        with (
            patch.object(scraper.rate_limiter, "wait"),
            patch.object(scraper.rate_limiter, "record_result") as record_result,
            patch.object(scraper.session, "request", return_value=response) as request_mock,
        ):
            result = scraper._request("GET", "https://example.com")

        self.assertIs(result, response)
        request_mock.assert_called_once_with(
            "GET",
            "https://example.com",
            timeout=(scraper.config.connect_timeout, scraper.config.read_timeout),
        )
        record_result.assert_called_once_with(True)

    def test_request_raises_blocked_error_on_waf_page(self):
        scraper = Scraper()
        response = MagicMock()
        response.status_code = 200
        response.text = "<html><body>Access Denied - your request was blocked</body></html>"
        response.raise_for_status.return_value = None

        with (
            patch.object(scraper.rate_limiter, "wait"),
            patch.object(scraper.rate_limiter, "record_result") as record_result,
            patch.object(scraper.session, "request", return_value=response),
        ):
            with self.assertRaises(BlockedError):
                scraper._request("GET", "https://example.com")

        # tenacity retries on BlockedError, so record_result(False) may fire
        # several times — assert the last call, not the count.
        self.assertTrue(record_result.called)
        self.assertEqual(record_result.call_args_list[-1], call(False))

    def test_request_passes_through_clean_response(self):
        scraper = Scraper()
        clean = "<table><tr><th>S.No</th></tr></table>" + "x" * 990
        response = MagicMock()
        response.status_code = 200
        response.text = clean
        response.raise_for_status.return_value = None

        with (
            patch.object(scraper.rate_limiter, "wait"),
            patch.object(scraper.rate_limiter, "record_result") as record_result,
            patch.object(scraper.session, "request", return_value=response),
        ):
            result = scraper._request("GET", "https://example.com")

        self.assertIs(result, response)
        record_result.assert_called_once_with(True)

    def test_is_blocked_detects_known_markers(self):
        for marker in BLOCKED_MARKERS:
            self.assertTrue(_is_blocked(f"<body>{marker}</body>"), marker)
        self.assertFalse(_is_blocked("<body><table>real content " + "x" * 1000 + "</table></body>"))

    def test_extract_basic_records_falls_back_to_first_table_and_skips_bad_rows(self):
        html = """
        <html>
        <body>
            <table>
                <tr>
                    <th>S.No</th><th>Reg No</th><th>Name</th><th>Father Name</th><th>Category</th>
                </tr>
                <tr>
                    <td>BAD</td><td>RPH001</td><td>Alice</td><td>Parent One</td><td>BPharm</td>
                </tr>
                <tr>
                    <td>2</td><td>RPH002</td><td>Bob</td><td>Parent Two</td>
                </tr>
                <tr>
                    <td>3</td><td>RPH003</td><td>Carol</td><td>Parent Three</td><td>DPharm</td><td>Ignored</td>
                </tr>
            </table>
        </body>
        </html>
        """

        scraper = Scraper()
        with patch.object(scraper, "_request", return_value=make_response(html)):
            records = scraper.extract_basic_records()

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].registration_number, "RPH001")
        self.assertIsNone(records[0].serial_number)
        self.assertEqual(records[1].registration_number, "RPH003")
        self.assertEqual(records[1].serial_number, 3)

    def test_extract_basic_records_returns_empty_when_no_table_exists(self):
        scraper = Scraper()
        with patch.object(
            scraper,
            "_request",
            return_value=make_response("<html><body>No table</body></html>"),
        ):
            records = scraper.extract_basic_records()

        self.assertEqual(records, [])

    def test_extract_detailed_info_returns_none_for_no_records_found(self):
        scraper = Scraper()
        with patch.object(
            scraper,
            "_request",
            return_value=make_response("<html><body>No Records Found</body></html>"),
        ):
            record = scraper.extract_detailed_info("RPH404")

        self.assertIsNone(record)

    def test_extract_detailed_info_parses_validity_education_and_photo(self):
        html = """
        <html>
        <body>
            <table>
                <tr>
                    <th>Registration No</th>
                    <th>Name</th>
                    <th>Father Name</th>
                    <th>Gender</th>
                    <th>Validity</th>
                    <th>Category</th>
                    <th>Status</th>
                    <th>Photo</th>
                </tr>
                <tr>
                    <td>RPH123</td>
                    <td>Jane Pharmacist</td>
                    <td>Parent Name</td>
                    <td>Female</td>
                    <td>31/12/2026</td>
                    <td>BPharm</td>
                    <td>Active</td>
                    <td><img id="imgPhotoMain" src="data:image/jpeg;base64,QUJDREVGRw==" /></td>
                </tr>
            </table>
            <table>
                <tr>
                    <th>Category</th><th>Board/University</th><th>College Name</th><th>College Address</th><th>From</th><th>To</th><th>HT No</th>
                </tr>
                <tr>
                    <th>BPharm</th><td>Osmania University</td><td>City College</td><td>Hyderabad</td><td>2014</td><td>2018</td><td>HT123</td>
                </tr>
                <tr>
                    <th>MPharm</th><td>Kakatiya University</td><td>North Campus</td><td>Warangal</td><td>2019</td><td></td><td></td>
                </tr>
            </table>
            <table>
                <tr>
                    <th>Address</th><th>State</th><th>District</th><th>Pin code</th>
                </tr>
                <tr>
                    <td>Clinic Street</td><td>Telangana</td><td>Hyderabad</td><td>500001</td>
                </tr>
            </table>
        </body>
        </html>
        """

        scraper = Scraper()
        with patch.object(scraper, "_request", return_value=make_response(html)):
            record = scraper.extract_detailed_info("RPH123")

        self.assertIsNotNone(record)
        self.assertEqual(record.registration_number, "RPH123")
        self.assertEqual(record.name, "Jane Pharmacist")
        self.assertEqual(record.father_name, "Parent Name")
        self.assertEqual(record.category, "BPharm")
        self.assertEqual(record.validity_date, "31/12/2026")
        self.assertEqual(
            record.education,
            [
                {
                    "Category": "BPharm",
                    "Board/University": "Osmania University",
                    "College Name": "City College",
                    "College Address": "Hyderabad",
                    "From": "2014",
                    "To": "2018",
                    "HT No": "HT123",
                },
                {
                    "Category": "MPharm",
                    "Board/University": "Kakatiya University",
                    "College Name": "North Campus",
                    "College Address": "Warangal",
                    "From": "2019",
                    "To": "",
                    "HT No": "",
                },
            ],
        )
        self.assertEqual(
            record.work_experience,
            {
                "Address": "Clinic Street",
                "State": "Telangana",
                "District": "Hyderabad",
                "Pin code": "500001",
            },
        )

    def test_extract_detailed_info_supports_legacy_education_headers(self):
        html = """
        <html>
        <body>
            <table>
                <tr>
                    <th>Registration No</th><th>Name</th>
                </tr>
                <tr>
                    <td>RPH123</td><td>Test User</td>
                </tr>
            </table>
            <table>
                <tr>
                    <th>S.No</th><th>Qualification</th><th>University</th><th>Year</th>
                </tr>
                <tr>
                    <td>1</td><td>B.Pharm</td><td>Osmania University</td><td>2018</td>
                </tr>
                <tr>
                    <td>2</td><td>M.Pharm</td><td>Kakatiya University</td>
                </tr>
            </table>
        </body>
        </html>
        """

        scraper = Scraper()
        with patch.object(scraper, "_request", return_value=make_response(html)):
            record = scraper.extract_detailed_info("RPH123")

        self.assertIsNotNone(record)
        self.assertEqual(
            record.education,
            [
                {
                    "Category": "B.Pharm",
                    "Board/University": "Osmania University",
                    "College Name": "",
                    "College Address": "",
                    "From": "",
                    "To": "",
                    "HT No": "",
                },
                {
                    "Category": "M.Pharm",
                    "Board/University": "Kakatiya University",
                    "College Name": "",
                    "College Address": "",
                    "From": "",
                    "To": "",
                    "HT No": "",
                },
            ],
        )

    def test_extract_detailed_info_returns_none_when_tables_are_missing(self):
        html = "<html><body><div>Valid Upto : 31-12-2026</div></body></html>"

        scraper = Scraper()
        with patch.object(scraper, "_request", return_value=make_response(html)):
            record = scraper.extract_detailed_info("RPH321")

        self.assertIsNone(record)


class TlsPinningTests(unittest.TestCase):
    """Certificate verification for the TGPC host (C4, audit F5/F10)."""

    def _pool_kwargs(self, adapter):
        adapter.init_poolmanager(1, 1)
        return adapter.poolmanager.connection_pool_kw

    def test_hostname_verification_is_enforced(self):
        # The TGPC certificate now carries a correct CN/SAN, so the C4
        # hostname exception is no longer used: the match stays ON.
        kwargs = self._pool_kwargs(_TGPCTLSAdapter())
        self.assertTrue(kwargs["ssl_context"].check_hostname)
        self.assertEqual(kwargs["ssl_context"].verify_mode, ssl.CERT_REQUIRED)
        # assert_hostname must be absent (urllib3 expects a hostname string
        # there, not a boolean) so the default host match applies.
        self.assertNotIn("assert_hostname", kwargs)

    def test_fingerprint_is_asserted_when_configured(self):
        adapter = _TGPCTLSAdapter(cert_fingerprint="ab" * 32)
        kwargs = self._pool_kwargs(adapter)
        self.assertEqual(kwargs.get("assert_fingerprint"), "ab" * 32)
        # Pinning adds a layer; it does not replace the hostname match.
        self.assertTrue(kwargs["ssl_context"].check_hostname)
        self.assertNotIn("assert_hostname", kwargs)

    def test_no_fingerprint_by_default(self):
        kwargs = self._pool_kwargs(_TGPCTLSAdapter())
        self.assertNotIn("assert_fingerprint", kwargs)
        self.assertTrue(kwargs["ssl_context"].check_hostname)

    def test_config_loads_fingerprint_from_env(self):
        with patch.dict(os.environ, {"TGPC_TLS_CERT_SHA256": "ab" * 32}):
            self.assertEqual(Config.load().tls_cert_sha256, "ab" * 32)

    def test_config_blanks_fingerprint_when_unset(self):
        with patch.dict(os.environ, {"TGPC_TLS_CERT_SHA256": "  "}):
            self.assertIsNone(Config.load().tls_cert_sha256)


class SourceHostTests(unittest.TestCase):
    """The source base URL must be the host whose certificate validates.

    The source's certificate carries a correct CN/SAN for the apex name
    `pharmacycouncil.telangana.gov.in` but NOT for the `www.` variant.
    Strict hostname verification therefore only works against the apex, so
    that is the default (CODE_REVIEW.md C4, resolved 2026-10).

    Verified live 2026-10-21: both hosts serve byte-identical payloads
    (bulk listing and detail pages match after normalising the per-session
    `jsessionid`), and the source does not care which host the `Referer`
    names. So the apex is strictly better — same content, valid cert.
    """

    def test_default_base_url_is_the_apex_host(self):
        self.assertEqual(
            Config().base_url,
            "https://pharmacycouncil.telangana.gov.in",
        )

    def test_default_base_url_has_no_www_prefix(self):
        self.assertNotIn("://www.", Config().base_url)

    def test_www_host_is_not_used_by_default(self):
        # Guard against a silent reversion to the www. host: that name has
        # no matching certificate, so it can only be reached by relaxing
        # hostname verification — the exact gap C4 asked us to close.
        self.assertNotIn("www.", Config().base_url)

    def test_scraper_mounts_the_base_host_on_the_verifying_adapter(self):
        # The TGPC host must be mounted on the fully-verifying adapter, and
        # the generic "https://" catch-all must not shadow it.
        scraper = Scraper()
        base_host = urlparse(scraper.config.base_url).hostname
        # requests keys adapters by URL prefix, not bare hostname.
        adapter = scraper.session.adapters[f"https://{base_host}"]
        self.assertIsInstance(adapter, _TGPCTLSAdapter)
        # The pool kwargs are built lazily; init the pool to read them.
        adapter.init_poolmanager(1, 1)
        kwargs = adapter.poolmanager.connection_pool_kw
        self.assertTrue(kwargs["ssl_context"].check_hostname)
        self.assertEqual(kwargs["ssl_context"].verify_mode, ssl.CERT_REQUIRED)

    def test_www_host_adapter_is_also_verifying(self):
        # Photos and redirects may still name the www. host, so it is
        # mounted too — and it must not be a relaxed adapter.
        scraper = Scraper()
        adapter = scraper.session.adapters["https://www.pharmacycouncil.telangana.gov.in"]
        self.assertIsInstance(adapter, _TGPCTLSAdapter)
        adapter.init_poolmanager(1, 1)
        kwargs = adapter.poolmanager.connection_pool_kw
        self.assertTrue(kwargs["ssl_context"].check_hostname)
        self.assertEqual(kwargs["ssl_context"].verify_mode, ssl.CERT_REQUIRED)

    def test_base_url_is_overridable_by_env(self):
        with patch.dict(os.environ, {"TGPC_BASE_URL": "https://example.test/"}):
            # Trailing slash is stripped so path joins never double up.
            self.assertEqual(Config.load().base_url, "https://example.test")

    def test_blank_base_url_env_falls_back_to_default(self):
        with patch.dict(os.environ, {"TGPC_BASE_URL": "   "}):
            self.assertEqual(Config.load().base_url, Config().base_url)

    def test_tgpc_host_helper_accepts_both_source_hosts(self):
        self.assertTrue(_is_tgpc_host("pharmacycouncil.telangana.gov.in"))
        self.assertTrue(_is_tgpc_host("www.pharmacycouncil.telangana.gov.in"))
        self.assertTrue(_is_tgpc_host("WWW.PharmacyCouncil.Telangana.gov.in"))

    def test_tgpc_host_helper_rejects_other_hosts(self):
        for host in (
            "evil.test",
            "pharmacycouncil.telangana.gov.in.evil.test",
            "localhost",
            "169.254.169.254",
        ):
            self.assertFalse(_is_tgpc_host(host))


if __name__ == "__main__":
    unittest.main()
