"""
Dispatch-list sync: council page -> R2 `dispatch/` prefix.

The council publishes one PDF per dispatch date at
`/site/dispatchListOfRegCert`, but the portal only serves what it finds in the
bucket (`dispatch/` prefix, see `ui/src/routes/api/dispatch/+server.ts`), so a
list the council has published is invisible until its PDF is copied into R2.

This module is that copy step and nothing else: fetch the page, normalise every
published file name to the portal's `DLDDMMYYYY.pdf` convention, download the
ones the bucket is missing, upload them, and confirm each object landed at the
expected size. Existing objects are never rewritten unless `--force` is passed,
because a bucket copy is at least as trustworthy as a fresh download.

The naming rules are load-bearing: `/api/dispatch/[name]` rejects anything that
is not `DL<6 digits>[A-Z].pdf`, so a file synced under the council's own name
(`Dispatch list 16-10-2026.pdf`) would sit in the bucket and never be listed.

Run: `python -m tgpc dispatch [--dry-run] [--force] [--write-fallback]`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Set, Tuple
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from tgpc.storage import R2Client
from tgpc.utils import load_credentials, setup_logging

logger = setup_logging("tgpc.dispatch")

# Non-www host: the certificate's SAN is the bare name, so `www.` fails
# hostname verification even though the same site answers on it.
SOURCE_HOST = "https://pharmacycouncil.telangana.gov.in"
DISPATCH_PAGE = f"{SOURCE_HOST}/site/dispatchListOfRegCert"

# R2 key prefix every dispatch PDF lives under (must match DISPATCH_PREFIX in
# ui/src/lib/dispatchNames.ts).
R2_PREFIX = "dispatch/"

# The stale-list fallback in the listing route is generated from the bucket, so
# it is refreshed from the same listing this module already has.
FALLBACK_TS = Path("ui/src/routes/api/dispatch/+server.ts")

# Smallest plausible dispatch PDF. A council list is tens of KB; anything under
# this is an error page or a truncated body, not a dispatch list.
MIN_PDF_BYTES = 1024

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Every naming scheme the council has used for these files, oldest first.
#   "01.02.2019 Dispatch List.pdf"          -> DL01022019.pdf
#   "03.06.2020 Dispatch List c.pdf"        -> DL03062020C.pdf
#   "11-09-2023 Dispatch List.pdf"          -> DL11092023.pdf
#   "Dispatch list 16-10-2026.pdf"          -> DL16102026.pdf
#   "Dispatch list 01-6-2024.pdf"           -> DL01062024.pdf
_SOURCE_PATTERNS = (
    re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4}) Dispatch List(?: (?P<s>[A-Za-z]))?\.pdf$", re.I),
    re.compile(r"^(?P<d>\d{1,2})-(?P<m>\d{1,2})-(?P<y>\d{4}) Dispatch List(?: (?P<s>[A-Za-z]))?\.pdf$", re.I),
    re.compile(
        r"^Dispatch list(?: of Registration Certificates)? (?P<d>\d{1,2})-(?P<m>\d{1,2})-(?P<y>\d{4})"
        r"(?: (?P<s>[A-Za-z]))?\.pdf$",
        re.I,
    ),
)


class DispatchPdf(NamedTuple):
    """One published dispatch list: portal name, source file name, PDF URL."""

    name: str
    filename: str
    url: str
    date: date


@dataclass
class SyncReport:
    """What one sync run saw and did."""

    published: int = 0
    already_present: int = 0
    missing: int = 0
    uploaded: int = 0
    failed: int = 0
    dry_run: bool = False
    newest: str = ""
    skipped_names: List[str] = field(default_factory=list)
    failed_names: List[str] = field(default_factory=list)


def dispatch_name(filename: str) -> Optional[str]:
    """Map a council file name to the portal's `DLDDMMYYYY[suffix].pdf` name.

    Returns None for names that are not dispatch lists or that encode an
    impossible date, so an unrelated PDF on the page cannot be published under
    a fabricated name.
    """
    name = filename.strip()
    for pattern in _SOURCE_PATTERNS:
        match = pattern.match(name)
        if not match:
            continue
        day, month, year = int(match["d"]), int(match["m"]), int(match["y"])
        try:
            date(year, month, day)
        except ValueError:
            return None
        suffix = (match["s"] or "").upper()
        return f"DL{day:02d}{month:02d}{year}{suffix}.pdf"
    return None


def parse_dispatch_page(html: str) -> Tuple[List[DispatchPdf], List[str]]:
    """Extract every dispatch PDF linked from the report page.

    Returns (entries, skipped) where `skipped` holds source file names that look
    like dispatch lists but do not match a known naming scheme. Entries are
    de-duplicated by portal name (the council occasionally links one date
    twice) and sorted oldest first.
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: dict[str, DispatchPdf] = {}
    skipped: List[str] = []

    for link in soup.find_all("a", href=True):
        href = str(link["href"]).split(";jsessionid")[0]
        path = href.split("?")[0]
        filename = unquote(path.rsplit("/", 1)[-1])
        if not filename.lower().endswith(".pdf"):
            continue
        if "dispatch" not in unquote(path).lower():
            continue
        name = dispatch_name(filename)
        if name is None:
            skipped.append(filename)
            continue
        if name in entries:
            continue
        url = path if path.startswith("http") else f"{SOURCE_HOST}/{path.lstrip('/')}"
        entries[name] = DispatchPdf(name=name, filename=filename, url=url, date=_entry_date(name))

    return sorted(entries.values(), key=lambda e: e.date), sorted(set(skipped))


def _entry_date(name: str) -> date:
    return date(int(name[6:10]), int(name[4:6]), int(name[2:4]))


def render_fallback(names: Sequence[str], per_line: int = 4) -> str:
    """Render the generated `const FALLBACK_FILES = [...]` block."""
    rows = ["  " + ", ".join(f"'{n}'" for n in names[i : i + per_line]) for i in range(0, len(names), per_line)]
    return "const FALLBACK_FILES = [\n" + ",\n".join(rows) + "\n];"


def update_fallback_file(path: Path, names: Sequence[str], today: Optional[date] = None) -> bool:
    """Rewrite the fallback list in the listing route from the live bucket.

    The route comment says to regenerate this from the bucket rather than edit
    it by hand, because an entry here is a file the page offers when R2 cannot
    be listed. Returns True when the file changed.
    """
    text = path.read_text(encoding="utf-8")
    block = render_fallback(sorted(names))
    updated, count = re.subn(r"const FALLBACK_FILES = \[.*?\n\];", block, text, flags=re.S)
    if count != 1:
        raise ValueError(f"{path}: expected exactly one FALLBACK_FILES block, found {count}")
    stamp = (today or date.today()).isoformat()
    updated, count = re.subn(
        r"(prefix dispatch/\) on )\d{4}-\d{2}-\d{2} - \d+ objects",
        rf"\g<1>{stamp} - {len(names)} objects",
        updated,
    )
    if count != 1:
        raise ValueError(f"{path}: could not update the provenance comment")
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def r2_client_from_env() -> R2Client:
    """Build the R2 client from env credentials (`load_credentials()` fills them)."""
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    missing = [
        key
        for key, value in (
            ("CLOUDFLARE_ACCOUNT_ID", account_id),
            ("R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY", secret_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing R2 credentials: {', '.join(missing)} (see `python -m tgpc creds list`)")
    return R2Client(f"https://{account_id}.r2.cloudflarestorage.com", access_key, secret_key)


class DispatchSyncer:
    """Copies newly published dispatch lists from the council into R2."""

    def __init__(
        self,
        client: Optional[R2Client] = None,
        session: Optional[requests.Session] = None,
        page_url: str = DISPATCH_PAGE,
    ) -> None:
        self._client = client
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.page_url = page_url

    @property
    def client(self) -> R2Client:
        if self._client is None:
            self._client = r2_client_from_env()
        return self._client

    def fetch_page(self, timeout: float = 60) -> str:
        response = self.session.get(self.page_url, timeout=timeout)
        response.raise_for_status()
        return response.text

    def bucket_names(self) -> Set[str]:
        """Portal names already in the bucket (keys stripped of the prefix)."""
        names = set()
        for obj in self.client.list_objects_all(R2_PREFIX):
            key = str(obj.get("Key", ""))
            if key.startswith(R2_PREFIX) and "/" not in key[len(R2_PREFIX) :]:
                names.add(key[len(R2_PREFIX) :])
        return names

    def download(self, entry: DispatchPdf, dest_dir: Path, timeout: float = 120) -> Path:
        """Download one PDF and refuse anything that is not a real PDF."""
        dest = dest_dir / entry.name
        with self.session.get(entry.url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            with open(dest, "wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)
        size = dest.stat().st_size
        with open(dest, "rb") as handle:
            magic = handle.read(5)
        if size < MIN_PDF_BYTES or not magic.startswith(b"%PDF"):
            dest.unlink(missing_ok=True)
            raise ValueError(f"{entry.filename}: not a PDF ({size} bytes, magic {magic!r})")
        return dest

    def upload(self, entry: DispatchPdf, path: Path) -> None:
        """Upload one PDF and verify the stored object size."""
        self.client.put_object(f"{R2_PREFIX}{entry.name}", path)
        head = self.client.head_object(f"{R2_PREFIX}{entry.name}")
        stored = int(head.get("ContentLength", -1))
        expected = path.stat().st_size
        if stored != expected:
            raise ValueError(f"{entry.name}: stored {stored} bytes, expected {expected}")

    def plan(
        self,
        entries: Sequence[DispatchPdf],
        present: Set[str],
        force: bool = False,
    ) -> List[DispatchPdf]:
        """Split published entries into (to upload)."""
        if force:
            return list(entries)
        return [entry for entry in entries if entry.name not in present]


def sync_dispatch(
    dry_run: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    write_fallback: bool = False,
    client: Optional[R2Client] = None,
    session: Optional[requests.Session] = None,
    workdir: Optional[Path] = None,
) -> SyncReport:
    """Sync every published dispatch list the bucket is missing.

    `dry_run` lists the work without touching the network, the bucket or the
    fallback list; `limit` caps uploads per run; `write_fallback` rewrites the
    listing route's stale-list from the resulting bucket listing.
    """
    if client is None:
        load_credentials()
    syncer = DispatchSyncer(client=client, session=session)
    report = SyncReport(dry_run=dry_run)

    html = syncer.fetch_page()
    entries, skipped = parse_dispatch_page(html)
    report.published = len(entries)
    report.skipped_names = skipped
    if skipped:
        logger.warning(f"dispatch: {len(skipped)} published file(s) not understood: {', '.join(skipped)}")
    if not entries:
        raise RuntimeError("dispatch: the source page listed no dispatch PDFs (layout change or outage?)")

    present = syncer.bucket_names()
    to_upload = syncer.plan(entries, present, force=force)
    report.already_present = len(entries) - len(to_upload)
    report.missing = len(to_upload)
    report.newest = entries[-1].name if entries else ""

    if limit is not None:
        to_upload = to_upload[: max(limit, 0)]

    if dry_run:
        return report

    workdir = workdir or Path("data/dispatch")
    workdir.mkdir(parents=True, exist_ok=True)

    for entry in to_upload:
        try:
            path = syncer.download(entry, workdir)
            try:
                syncer.upload(entry, path)
            finally:
                path.unlink(missing_ok=True)
            report.uploaded += 1
            logger.info(f"dispatch: uploaded {entry.name} ({entry.date.isoformat()})")
        except requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) == 404:
                logger.debug(f"dispatch: {entry.name} linked but not hosted by the council (HTTP 404)")
            else:
                report.failed += 1
                report.failed_names.append(entry.name)
                logger.warning(f"dispatch: {entry.name} failed: {exc}")
        except Exception as exc:
            report.failed += 1
            report.failed_names.append(entry.name)
            logger.warning(f"dispatch: {entry.name} failed: {exc}")

    if write_fallback:
        try:
            names = syncer.bucket_names()
            changed = update_fallback_file(FALLBACK_TS, sorted(names))
            logger.info(f"dispatch: fallback list {'updated' if changed else 'already current'} ({len(names)} files)")
        except Exception as exc:
            logger.warning(f"dispatch: fallback list not updated: {exc}")

    return report
