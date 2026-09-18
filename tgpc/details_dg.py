"""
Capture + save pharmacist details from the TGPC `getdetailsdg` form flow.

Flow (verified live 2026-09-17):
  1. GET {base}/pharmacy/getdetailsdg -> JSESSIONID cookie + Struts `token`
  2. GET {base}/captchaimage.jsp (ROOT path, same cookies + Referer) -> 170x50 JPEG
  3. POST {base}/pharmacy/getdetailsviewdg.action
     (registration_no, recapcha [note spelling], struts.token.name=token,
      token, submit=Submit) -> `Pharmacist Details` table

Redundancy (re-fetch is expensive — captcha + throttle per record):
  L1 local   data/dg_raw/{REG}.json + data/dg_contacts.jsonl + checkpoint (fsync)
  L2 Supabase `rph` new columns (slice 2 upsert; this slice writes the jsonl)
  L3 R2       tgpc/dg-raw/{REG}.json + tgpc/dg_contacts.jsonl (best-effort push)
  L4 GDrive   via existing rclone path (slice 2)

Checkpoint advances only after L1 fsync succeeds, so a crash loses at most
one record and `--resume` never re-fetches completed IDs.
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from tgpc.progress import ProgressBar, step
from tgpc.scraper import _TGPCTLSAdapter
from tgpc.utils import BlockedError, Config, setup_logging

logger = setup_logging("tgpc.details_dg")

FORM_PATH = "/pharmacy/getdetailsdg"
CAPTCHA_PATH = "/captchaimage.jsp"  # root — /pharmacy/captchaimage.jsp 404s
VIEW_PATH = "/pharmacy/getdetailsviewdg.action"

REG_RE = re.compile(r"^(TS|TG|TSDR|TGDR)\d+$", re.I)
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CAPTCHA_RE = re.compile(r"^[A-Z0-9]{4,8}$")
DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")

# Normalized DG header -> canonical field. Everything else is kept verbatim
# in `raw_cells` but not promoted to columns.
HEADER_MAP = {
    "registration no": "registration_number",
    "gender": "gender",
    "pharmacist name": "name",
    "father name": "father_name",
    "date of birth": "dob",
    "category": "category",
    "date of registration": "date_of_registration",
    "renewal validity": "renewal_validity",
    "address": "home_address",
    "state": "home_state",
    "working/studying address": "work_study_address",
    "working/studying state": "work_study_state",
    "mobile no": "mobile_no",
    "email id": "email_id",
}

GENDER_MAP = {"m": "Male", "f": "Female", "male": "Male", "female": "Female"}


# Same WAF markers as scraper.BLOCKED_MARKERS MINUS "captcha": the DG form
# and captcha-fail pages legitimately mention captchas, so "captcha" alone
# must not count as a block here (a real WAF page still matches the rest).
DG_BLOCKED_MARKERS = (
    "access denied",
    "forbidden",
    "blocked",
    "suspicious",
    "security check",
    "unusual traffic",
)


def _is_blocked_dg(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in DG_BLOCKED_MARKERS)


class CaptchaNeeded(Exception):
    """Raised when no automated solver is available for a captcha image."""


class DgDetailError(Exception):
    """Parse/validation failure — caller quarantines instead of upserting."""

    def __init__(self, message: str = "", html: str = "", terminal: bool = False):
        super().__init__(message)
        self.html = html
        # Terminal = retrying is futile (record gap in DG backend). Transient
        # (captcha miss, block) is retried with a fresh session instead.
        self.terminal = terminal


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_header(value: str) -> str:
    return normalize_space(value).lower()


def normalize_reg(value: str) -> str:
    return normalize_space(value).upper()


def normalize_gender(value: str) -> str:
    return GENDER_MAP.get(normalize_space(value).lower(), "")


def valid_mobile(value: str) -> bool:
    return bool(MOBILE_RE.match(normalize_space(value)))


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(normalize_space(value).lower()))


def valid_dmy(value: str) -> bool:
    """True for a real calendar DD-MM-YYYY date (no conversion, per pipeline norm)."""
    m = DATE_RE.match(normalize_space(value))
    if not m:
        return False
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        datetime(yyyy, mm, dd)
    except ValueError:
        return False
    return True


def solve_captcha(image_bytes: bytes) -> str:
    """OCR a 6-char DG captcha via majority vote across preprocess variants.

    Benched 10/12 first-pass on live captchas (2026-09-17,
    scripts/dg_captcha_bench.py). Misses are dropped chars, never confident
    wrong reads — the server rejects them and fetch_one() retries with a
    fresh session, so saved-data accuracy stays 100% via parse/validate gates.
    Raises CaptchaNeeded if no solver is installed, DgDetailError if no
    variant yields a usable code.
    """
    try:
        import pytesseract  # type: ignore
    except ImportError as e:
        raise CaptchaNeeded("pytesseract not installed — use --captcha manual") from e
    try:
        from collections import Counter
        from io import BytesIO

        from PIL import Image
    except ImportError as e:
        raise CaptchaNeeded("Pillow not installed") from e

    whitelist = " -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    base = Image.open(BytesIO(image_bytes)).convert("L")
    votes = []
    # Threshold 180 won the bench 10/12 (2026-09-17); pooling other
    # thresholds diluted the majority back to 9/12, so single threshold.
    for scale in (2, 3):
        binarized = base.resize((base.width * scale, base.height * scale)).point(lambda p: 255 if p > 180 else 0)
        for psm in ("--psm 8", "--psm 7"):
            text = pytesseract.image_to_string(binarized, config=psm + whitelist)
            code = re.sub(r"\s+", "", text.upper())
            if CAPTCHA_RE.match(code):
                votes.append(code)
    if not votes:
        raise DgDetailError("OCR produced no usable code")
    return Counter(votes).most_common(1)[0][0]


def parse_dg_html(html: str, expected_reg: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Parse a `getdetailsviewdg` response.

    Returns (parsed, raw_cells). Raises DgDetailError on captcha-fail pages,
    not-found pages, echoed-reg mismatch, or missing identity fields.
    """
    if _is_blocked_dg(html):
        raise BlockedError("Blocked response from source")
    if "You are not Authorized" in html:
        # Proven persistent per-record DG backend gap (TS000002: 4 attempts /
        # 35 min, fresh sessions + human-read captchas, still refused) while
        # the same record exists in getsearchpharmacist. Terminal: no retry.
        raise DgDetailError("Not authorized for this record (terminal backend gap)", terminal=True)
    if "Requested Page Not Found" in html or "No Records Found" in html:
        raise DgDetailError("Not-found page", terminal=True)
    if "Get Pharmacist Details" in html and "recapcha" in html and "Pharmacist Details" not in html:
        raise DgDetailError("Captcha-fail (form re-rendered, no details table)")

    soup = BeautifulSoup(html, "html.parser")
    raw_cells: Dict[str, str] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            # Positional alignment — NEVER drop empty cells: a row like
            # [H1, "", H2, ""] must map H1->"" and H2->"", not H1->H2.
            cells = [normalize_space(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
            if len(cells) == 4:
                raw_cells[normalize_header(cells[0])] = cells[1]
                raw_cells[normalize_header(cells[2])] = cells[3]
            elif len(cells) == 2:
                raw_cells[normalize_header(cells[0])] = cells[1]

    if "registration no" not in raw_cells:
        raise DgDetailError("No details table found")

    echoed = normalize_reg(raw_cells.get("registration no", ""))
    if echoed != normalize_reg(expected_reg):
        raise DgDetailError(f"Echoed reg {echoed!r} != requested {expected_reg!r}")

    parsed = {field: raw_cells.get(header, "") for header, field in HEADER_MAP.items()}
    parsed["registration_number"] = echoed
    parsed["gender"] = normalize_gender(parsed.get("gender", ""))
    parsed["mobile_no"] = normalize_space(parsed.get("mobile_no", ""))
    parsed["email_id"] = normalize_space(parsed.get("email_id", "")).lower()

    for key in ("registration_number", "name", "category"):
        if not parsed.get(key):
            raise DgDetailError(f"Missing identity field: {key}")
    return parsed, raw_cells


def validate_parsed(parsed: Dict[str, str]) -> List[str]:
    """Return a list of validation problems (empty = accept)."""
    problems = []
    if not REG_RE.match(parsed.get("registration_number", "")):
        problems.append("bad_reg")
    if not parsed.get("name"):
        problems.append("missing_name")
    if parsed.get("gender") not in ("Male", "Female", ""):
        problems.append("bad_gender")
    if parsed.get("dob") and not valid_dmy(parsed["dob"]):
        problems.append("bad_dob")
    if parsed.get("date_of_registration") and not valid_dmy(parsed["date_of_registration"]):
        problems.append("bad_dor")
    if parsed.get("renewal_validity") and not valid_dmy(parsed["renewal_validity"]):
        problems.append("bad_validity")
    if parsed.get("mobile_no") and not valid_mobile(parsed["mobile_no"]):
        problems.append("bad_mobile")
    if parsed.get("email_id") and not valid_email(parsed["email_id"]):
        problems.append("bad_email")
    return problems


def identity_matches(parsed: Dict[str, str], reference: Optional[Dict[str, str]]) -> bool:
    """Guard vs rph.json identity. None reference = pass. See _match_detail."""
    ok, _ = _match_detail(parsed, reference)
    return ok


def _match_detail(parsed: Dict[str, str], reference: Optional[Dict[str, str]]) -> Tuple[bool, List[str]]:
    """Name-mandatory 2-of-3 guard with truncation tolerance.

    Rationale from live data: the bulk table truncates long father names
    (TS000249 'Devarakonda Lakshmi Naray…' vs full DG value) and categories
    get corrected upstream (TS000235 BPharm->DPharm). Strict exact-match
    quarantined the BETTER data. So: name must match exactly; father passes
    on exact or prefix-either-way (min 10 chars, truncation-proof); category
    must match unless name+father both pass (logged as a correction note).
    Returns (accept, notes) — notes explain every divergence for audit.
    """
    if not reference:
        return True, []
    notes: List[str] = []
    name_ok = normalize_space(parsed.get("name", "")).lower() == normalize_space(reference.get("name", "")).lower()
    if not name_ok:
        return False, ["name_mismatch"]

    p_father = normalize_space(parsed.get("father_name", "")).lower()
    r_father = normalize_space(reference.get("father_name", "")).lower()
    father_ok = p_father == r_father
    if not father_ok and p_father and r_father and min(len(p_father), len(r_father)) >= 10:
        if p_father.startswith(r_father) or r_father.startswith(p_father):
            father_ok = True
            notes.append(f"father_truncated:{reference.get('father_name', '')}")
    if not father_ok:
        notes.append("father_mismatch")

    p_cat = normalize_space(parsed.get("category", ""))
    r_cat = normalize_space(reference.get("category", ""))
    cat_ok = p_cat.lower() == r_cat.lower()
    if not cat_ok:
        notes.append(f"category_changed:{r_cat}->{p_cat}")

    if father_ok or cat_ok:
        return True, notes
    return False, notes


def load_checkpoint(path: Path) -> Dict:
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            state.setdefault("completed", [])
            state.setdefault("failed", {})
            state.setdefault("failed_terminal", {})
            state.setdefault("quarantined", [])
            return state
        except Exception:
            pass
    return _fresh_state()


def save_checkpoint_atomic(path: Path, state: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def _attach_file_log(path: Path) -> None:
    """Mirror tgpc.details_dg logs to a file for `tail -f` (no dup handlers)."""
    import logging

    path.parent.mkdir(parents=True, exist_ok=True)
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(path):
            return
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


def _heal_jsonl(path: Path) -> int:
    """Drop a crash-torn trailing partial line. Returns lines removed (0/1).

    Appends are fsync'd per record, but a kill between write() and the
    newline still leaves a torn last line that would break every downstream
    json.loads. Heal on startup (resume) so one crash can't poison the file.
    """
    if not path.exists():
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception:
        return 0
    if not lines:
        return 0
    last = lines[-1]
    try:
        json.loads(last)
        return 0
    except Exception:
        pass
    path.write_text("".join(lines[:-1]), encoding="utf-8")
    logger.warning(f"Healed torn last line in {path.name}")
    return 1


class DgFetcher:
    """Session-bound DG fetcher (one session per worker)."""

    def __init__(self, config: Optional[Config] = None, min_delay: float = 3.0):
        self.config = config or Config.load()
        self.min_delay = min_delay
        self.session = requests.Session()
        adapter = _TGPCTLSAdapter(pool_connections=5, pool_maxsize=5, max_retries=3)
        self.session.mount("https://www.pharmacycouncil.telangana.gov.in", adapter)
        self.session.mount("https://pharmacycouncil.telangana.gov.in", adapter)
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Referer": f"{self.config.base_url}{FORM_PATH}",
            }
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _get(self, url: str, **kwargs) -> requests.Response:
        time.sleep(self.min_delay)
        resp = self.session.get(url, timeout=(self.config.connect_timeout, self.config.read_timeout), **kwargs)
        resp.raise_for_status()
        if resp.headers.get("Content-Type", "").startswith("text") and _is_blocked_dg(resp.text):
            raise BlockedError("Blocked response from source")
        return resp

    def fetch_one(
        self,
        reg_no: str,
        captcha_solver: Optional[Callable[[bytes], str]] = None,
        max_captcha_attempts: int = 3,
    ) -> Tuple[str, Dict]:
        """Fetch the details-view HTML for one reg. Returns (html, meta)."""
        reg_no = normalize_reg(reg_no)
        meta: Dict = {"reg": reg_no, "attempts": 0}
        last_error: Optional[Exception] = None
        for attempt in range(1, max_captcha_attempts + 1):
            meta["attempts"] = attempt
            t0 = time.monotonic()
            form = self._get(f"{self.config.base_url}{FORM_PATH}")
            t_form = time.monotonic() - t0
            m = re.search(r'name="token" value="([^"]+)"', form.text)
            if not m:
                raise DgDetailError("Form token not found")
            token = m.group(1)

            t1 = time.monotonic()
            cap = self._get(
                f"{self.config.base_url}{CAPTCHA_PATH}",
                headers={"Referer": f"{self.config.base_url}{FORM_PATH}"},
            )
            t_captcha = time.monotonic() - t1
            solver = captcha_solver or solve_captcha
            try:
                code = solver(cap.content)
            except CaptchaNeeded:
                raise
            except DgDetailError as e:
                last_error = e
                continue

            t2 = time.monotonic()
            time.sleep(self.min_delay)
            resp = self.session.post(
                f"{self.config.base_url}{VIEW_PATH}",
                data={
                    "registration_no": reg_no,
                    "recapcha": code,
                    "struts.token.name": "token",
                    "token": token,
                    "submit": "Submit",
                },
                headers={"Referer": f"{self.config.base_url}{FORM_PATH}"},
                timeout=(self.config.connect_timeout, self.config.read_timeout),
            )
            resp.raise_for_status()
            t_post = time.monotonic() - t2
            meta.update(
                {
                    "captcha_text": code,
                    "ms": {
                        "form": int(t_form * 1000),
                        "captcha": int(t_captcha * 1000),
                        "post": int(t_post * 1000),
                    },
                }
            )
            try:
                parse_dg_html(resp.text, reg_no)
            except DgDetailError as e:
                # Captcha rejected -> form re-render; retry with a fresh session state.
                if "Captcha-fail" in str(e):
                    last_error = e
                    continue
                e.html = resp.text
                raise
            return resp.text, meta
        err = DgDetailError(f"Captcha failed after {max_captcha_attempts} attempts: {last_error}")
        err.html = ""
        raise err


def _fresh_state() -> Dict:
    return {"completed": [], "failed": {}, "failed_terminal": {}, "quarantined": []}


def _record_failure(state: Dict, stats: Dict, reg: str, reason: str, terminal: bool) -> None:
    state["failed"][reg] = reason[:200]
    if terminal:
        state["failed_terminal"][reg] = reason[:200]
    stats["fail_by_reason"][reason] = stats["fail_by_reason"].get(reason, 0) + 1


# --- Cloud sync (L2-L4) ----------------------------------------------------
# Local L1 files are the crash buffer; these push them to redundant cloud
# copies. Cloud failures never block the checkpoint — upserts are idempotent
# on registration_number, so the next batch or end-of-run push heals gaps.

# Separate public table (migration: tgpc/dg_migration.sql). DG writes never
# touch rph base rows — payloads carry only DG columns keyed by reg number.
DG_TABLE = "rph_dg_contacts"

DG_COLUMNS = (
    "dob",
    "date_of_registration",
    "renewal_validity",
    "home_address",
    "home_state",
    "work_study_address",
    "work_study_state",
    "mobile_no",
    "email_id",
)


def build_supabase_payload(parsed: Dict[str, str], fetched_at: str, serial: object = None) -> Dict[str, object]:
    """Map a validated parsed DG row to rph_dg_contacts columns (+migration file)."""
    payload: Dict[str, object] = {"registration_number": parsed.get("registration_number", "")}
    payload["serial_number"] = serial
    for col in DG_COLUMNS:
        payload[col] = parsed.get(col, "") or ""
    payload["dg_fetched_at"] = fetched_at
    return payload


def upsert_dg_batch(payloads: List[Dict[str, object]]) -> Tuple[int, str]:
    """Upsert DG payloads to Supabase rph_dg_contacts. Returns (count, error)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        return 0, "missing SUPABASE_URL/SECRET_KEY"
    try:
        from supabase import create_client  # type: ignore
    except ImportError as e:
        return 0, f"supabase lib missing: {e}"
    try:
        sb = create_client(url, key)
        sb.table(DG_TABLE).upsert(payloads, on_conflict="registration_number").execute()
        return len(payloads), ""
    except Exception as e:
        return 0, str(e)[:200]


def push_file_to_r2(local_path: Path, key: str) -> bool:
    """PUT one local file to R2 tgpc/{key}. False when creds/tooling missing."""
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not all([account, access_key, secret_key]):
        logger.warning("R2 credentials missing — skipping R2 push (L1 local retained)")
        return False
    endpoint = f"https://{account}.r2.cloudflarestorage.com"
    env = {**os.environ, "AWS_ACCESS_KEY_ID": access_key or "", "AWS_SECRET_ACCESS_KEY": secret_key or ""}
    try:
        result = subprocess.run(
            [
                "aws",
                "s3api",
                "put-object",
                "--endpoint-url",
                endpoint,
                "--region",
                "auto",
                "--bucket",
                "tgpc",
                "--key",
                key,
                "--body",
                str(local_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except FileNotFoundError:
        logger.error("aws CLI not found — skipping R2 push")
        return False
    except Exception as e:
        logger.error(f"R2 push error for {key}: {e}")
        return False
    if result.returncode != 0:
        logger.error(f"R2 push failed for {key}: {result.stderr.strip()}")
        return False
    return True


def push_dg_to_gdrive(local_path: Path, remote_name: str) -> bool:
    """Copy one file to gdrive:tgpc/{remote_name} via rclone (config never lingers)."""
    import base64
    import tempfile

    b64 = os.environ.get("RCLONE_GDRIVE_CONFIG")
    if not b64:
        logger.warning("RCLONE_GDRIVE_CONFIG missing — skipping GDrive push")
        return False
    try:
        with tempfile.NamedTemporaryFile(suffix=".conf", delete=False) as tmp:
            tmp.write(base64.b64decode(b64))
            conf = tmp.name
        try:
            result = subprocess.run(
                ["rclone", "copyto", str(local_path), f"gdrive:tgpc/{remote_name}"],
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "RCLONE_CONFIG": conf},
            )
        finally:
            Path(conf).unlink(missing_ok=True)
    except FileNotFoundError:
        logger.error("rclone not installed — skipping GDrive push")
        return False
    except Exception as e:
        logger.error(f"GDrive push error for {remote_name}: {e}")
        return False
    if result.returncode != 0:
        logger.error(f"GDrive push failed for {remote_name}: {result.stderr.strip()}")
        return False
    return True


def push_dg_to_sb_storage(local_path: Path, object_name: str) -> bool:
    """Upload one file to Supabase Storage tgpc/{object_name} (x-upsert)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        logger.warning("Supabase credentials missing — skipping Storage push")
        return False
    try:
        import requests as rq

        with open(local_path, "rb") as f:
            resp = rq.post(
                f"{url}/storage/v1/object/tgpc/{object_name}",
                headers={"Authorization": f"Bearer {key}", "apikey": key, "x-upsert": "true"},
                data=f,
                timeout=300,
            )
    except Exception as e:
        logger.error(f"Supabase Storage push error for {object_name}: {e}")
        return False
    if not resp.ok:
        logger.error(f"Supabase Storage push failed for {object_name}: {resp.status_code}")
        return False
    return True


def push_dg_to_r2(file_paths: List[Path], prefix: str = "dg-raw") -> bool:
    """Best-effort R2 push for DG artifacts (L3). False when creds missing."""
    ok = True
    for path in file_paths:
        if not path.is_file():
            continue
        if not push_file_to_r2(path, f"{prefix}/{path.name}"):
            ok = False
    return ok


def sync_cloud_snapshot(out_jsonl: Path, raw_dir: Path, regs: List[str]) -> Dict[str, bool]:
    """End-of-run push: jsonl to R2 + GDrive + Supabase Storage, raws to R2.

    Writes a timestamped jsonl copy alongside the rolling `latest` key so a
    short/failed run can never clobber a good snapshot — history is kept.
    Returns per-destination success flags for stats/logs.
    """
    results: Dict[str, bool] = {}
    if out_jsonl.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        results["r2_jsonl"] = push_file_to_r2(out_jsonl, "dg-contacts/dg_contacts.jsonl")
        results["r2_jsonl_stamped"] = push_file_to_r2(out_jsonl, f"dg-contacts/dg_contacts_{stamp}.jsonl")
        results["gdrive_jsonl"] = push_dg_to_gdrive(out_jsonl, "dg_contacts.jsonl")
        results["sb_storage_jsonl"] = push_dg_to_sb_storage(out_jsonl, "dg_contacts.jsonl")
    raw_ok = True
    for reg in regs:
        raw_path = raw_dir / f"{reg}.json"
        if raw_path.exists() and not push_file_to_r2(raw_path, f"dg-raw/{reg}.json"):
            raw_ok = False
    results["r2_raws"] = raw_ok
    return results


def run_fetch(
    reg_ids: List[str],
    out_jsonl: Path,
    raw_dir: Path,
    checkpoint_path: Path,
    stats_path: Path,
    quarantine_path: Path,
    reference: Optional[Dict[str, Dict[str, str]]] = None,
    workers: int = 1,
    min_delay: float = 3.0,
    max_captcha_attempts: int = 3,
    resume: bool = True,
    retry_terminal: bool = False,
    captcha_solver: Optional[Callable[[bytes], str]] = None,
    fetcher_factory: Optional[Callable[[], DgFetcher]] = None,
    push_r2: bool = False,
    sync_cloud: bool = False,
    sync_every: int = 50,
    max_records: Optional[int] = None,
) -> Dict:
    """Fetch + validate + save L1 for a list of reg IDs. Returns stats dict.

    sync_cloud: upsert validated rows to Supabase rph_dg_contacts every sync_every
    successes + push cloud snapshot (R2/GDrive/SB Storage) at end of run.
    Cloud failures never block the L1 checkpoint (idempotent on resume).
    sync_cloud requires reference (fail-closed: no orphan-row inserts).
    max_records: stop after this many newly processed records (bounded runs).

    Live watch (same dir as checkpoint_path):
      dg_fetch.log  appended per record — `tail -f` it.
      dg_live.json  overwritten per record — `watch -n1 cat` it.
      dg_stop       `touch` it to halt after the current record (checkpoint-safe).
    """
    if workers != 1:
        raise ValueError("Slice 1 supports workers=1 only (slow-safe pilot)")
    if sync_cloud and reference is None:
        raise ValueError("sync_cloud requires reference (rph.json) — refusing orphan-row inserts")
    live_path = checkpoint_path.parent / "dg_live.json"
    stop_path = checkpoint_path.parent / "dg_stop"
    log_path = checkpoint_path.parent / "dg_fetch.log"
    history_path = checkpoint_path.parent / "dg_history.jsonl"
    _attach_file_log(log_path)
    if resume:
        _heal_jsonl(out_jsonl)
        _heal_jsonl(quarantine_path)
        _heal_jsonl(history_path)
    state = load_checkpoint(checkpoint_path) if resume else _fresh_state()
    done = set(state.get("completed", []))
    terminal = set(state.get("failed_terminal", {}))
    if not retry_terminal:
        skip = done | terminal
    else:
        skip = done
    todo = [normalize_reg(r) for r in reg_ids if normalize_reg(r) not in skip]
    if max_records is not None:
        todo = todo[: max(0, max_records)]

    live: Dict = {"run": "fetch-dg", "status": "running"}

    def write_live(**fields: object) -> None:
        live.update(fields)
        live["updated_at"] = utcnow()
        try:
            tmp = live_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(live, indent=1), encoding="utf-8")
            tmp.replace(live_path)
        except Exception:
            pass

    stats: Dict = {
        "total": len(reg_ids),
        "already_done": len(reg_ids) - len(todo),
        "done": 0,
        "failed": 0,
        "quarantined": 0,
        "fail_by_reason": {},
        "captcha_firstpass_ok": 0,
        "captcha_retries": 0,
        "sb_upserted": 0,
        "sb_batches_failed": 0,
        "cloud_snapshot": {},
        "started_at": utcnow(),
    }
    pending_payloads: List[Dict[str, object]] = []
    deferred_payloads: List[Dict[str, object]] = []

    def flush_supabase_batch() -> None:
        # Failed batches are DEFERRED, never dropped: retried once at end of
        # run; anything still failing is reported in stats (rows stay in L1).
        if not pending_payloads:
            return
        count, err = upsert_dg_batch(pending_payloads)
        if err:
            stats["sb_batches_failed"] += 1
            deferred_payloads.extend(pending_payloads)
            logger.error(f"Supabase batch deferred ({len(pending_payloads)} rows): {err}")
        else:
            stats["sb_upserted"] += count
            step(f"upserted {count} DG rows to Supabase")
        pending_payloads.clear()

    def retry_deferred() -> None:
        if not deferred_payloads:
            return
        step(f"retrying {len(deferred_payloads)} deferred DG rows")
        count, err = upsert_dg_batch(deferred_payloads)
        if err:
            stats["sb_batches_failed"] += 1
            stats["sb_pending"] = len(deferred_payloads)
            stats["sb_pending_regs"] = [p["registration_number"] for p in deferred_payloads][:50]
            logger.error(f"Deferred DG upsert still failing: {err} — rows retained in L1 jsonl")
        else:
            stats["sb_upserted"] += count
        deferred_payloads.clear()

    def save_stats() -> None:
        stats["updated_at"] = utcnow()
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = stats_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        tmp.replace(stats_path)

    fetcher = fetcher_factory() if fetcher_factory else DgFetcher(min_delay=min_delay)
    start_all = time.monotonic()
    processed = 0

    def live_snapshot(current_reg: str, event: str) -> None:
        elapsed = time.monotonic() - start_all
        rate = processed / elapsed * 60 if elapsed > 0 else 0
        remaining = len(todo) - processed
        write_live(
            status="running",
            current_reg=current_reg,
            serial_number=serial_of(current_reg),
            index=f"{processed}/{len(todo)}",
            event=event,
            done=stats["done"],
            failed=stats["failed"],
            quarantined=stats["quarantined"],
            sb_upserted=stats.get("sb_upserted", 0),
            records_per_min=round(rate, 1),
            eta_mins=round(remaining / (rate / 60), 1) if rate > 0 else None,
        )

    def record_history(
        reg: str, outcome: str, reason: str = "", ms: int = 0, captcha_attempts: int = 0, serial: object = None
    ) -> None:
        """Permanent per-record memory (all-time, never reset per run)."""
        append_jsonl(
            history_path,
            {
                "registration_number": reg,
                "serial_number": serial,
                "outcome": outcome,  # saved | failed | quarantined
                "reason": reason,
                "ms": ms,
                "captcha_attempts": captcha_attempts,
                "at": utcnow(),
            },
        )

    def serial_of(reg: str) -> object:
        """Tracker serial from rph.json reference (None when unknown)."""
        ref = (reference or {}).get(reg)
        return ref.get("serial_number") if isinstance(ref, dict) else None

    write_live(status="running", total=len(todo), already_done=stats["already_done"], event="started")
    logger.info(f"DG fetch started: {len(todo)} to process ({stats['already_done']} already done)")
    with ProgressBar(total=len(todo), label="DG fetch", cadence=1) as bar:
        for reg in todo:
            if stop_path.exists():
                try:
                    stop_path.unlink()
                except OSError:
                    pass
                stats["stopped"] = True
                logger.warning(f"STOP requested — halting after {processed} records (checkpoint-safe)")
                write_live(status="stopped", event="stop requested")
                bar.update(0, detail="stopped")
                break
            bar.set_detail(reg)
            step(f"fetching {reg}")
            logger.info(f"fetching {reg} ({processed + 1}/{len(todo)})")
            live_snapshot(reg, "fetching")
            t0 = time.monotonic()
            try:
                html, meta = fetcher.fetch_one(
                    reg, captcha_solver=captcha_solver, max_captcha_attempts=max_captcha_attempts
                )
            except CaptchaNeeded as e:
                stats["failed"] += 1
                state["failed"][reg] = "captcha_solver_missing"
                stats["fail_by_reason"]["captcha_solver_missing"] = (
                    stats["fail_by_reason"].get("captcha_solver_missing", 0) + 1
                )
                logger.error(f"{reg}: {e}")
                bar.update(1, detail=f"{reg} solver-missing")
                save_stats()
                save_checkpoint_atomic(checkpoint_path, state)
                processed += 1
                live_snapshot(reg, "solver-missing")
                record_history(reg, "failed", "captcha_solver_missing", serial=serial_of(reg))
                continue
            except (BlockedError, DgDetailError) as e:
                stats["failed"] += 1
                reason = str(e)[:200] or type(e).__name__
                _record_failure(state, stats, reg, reason, getattr(e, "terminal", False))
                html = getattr(e, "html", "")
                if html:
                    failed_dir = checkpoint_path.parent / "dg_failed"
                    failed_dir.mkdir(parents=True, exist_ok=True)
                    (failed_dir / f"{reg}.html").write_text(html, encoding="utf-8")
                    logger.error(f"{reg}: {e} (page saved to {failed_dir / f'{reg}.html'})")
                else:
                    logger.error(f"{reg}: {e}")
                bar.update(1, detail=f"{reg} {type(e).__name__}")
                save_stats()
                save_checkpoint_atomic(checkpoint_path, state)
                processed += 1
                live_snapshot(reg, type(e).__name__)
                record_history(reg, "failed", reason, serial=serial_of(reg))
                continue
            except Exception as e:
                stats["failed"] += 1
                state["failed"][reg] = f"unexpected: {e}"[:200]
                stats["fail_by_reason"]["unexpected"] = stats["fail_by_reason"].get("unexpected", 0) + 1
                logger.error(f"{reg}: unexpected {e}")
                bar.update(1, detail=f"{reg} unexpected")
                save_stats()
                save_checkpoint_atomic(checkpoint_path, state)
                processed += 1
                live_snapshot(reg, "unexpected")
                record_history(reg, "failed", "unexpected", serial=serial_of(reg))
                continue

            if meta.get("attempts", 1) == 1:
                stats["captcha_firstpass_ok"] += 1
            else:
                stats["captcha_retries"] += meta.get("attempts", 1) - 1

            try:
                parsed, raw_cells = parse_dg_html(html, reg)
            except (BlockedError, DgDetailError) as e:
                stats["failed"] += 1
                reason = str(e)[:200] or type(e).__name__
                _record_failure(state, stats, reg, reason, getattr(e, "terminal", False))
                failed_dir = checkpoint_path.parent / "dg_failed"
                failed_dir.mkdir(parents=True, exist_ok=True)
                (failed_dir / f"{reg}.html").write_text(html, encoding="utf-8")
                bar.update(1, detail=f"{reg} parse-fail")
                save_stats()
                save_checkpoint_atomic(checkpoint_path, state)
                processed += 1
                live_snapshot(reg, "parse-fail")
                record_history(reg, "failed", reason, serial=serial_of(reg))
                continue

            problems = validate_parsed(parsed)
            ref = (reference or {}).get(reg)
            if reference is not None and ref is None:
                # Fail-closed: a DG row for an ID absent from rph.json must
                # never create a partial orphan row in Supabase on upsert.
                problems = [*problems, "unknown_reg"]
            guard_ok, guard_notes = _match_detail(parsed, ref)
            if problems or not guard_ok:
                extra = [f"guard:{n}" for n in guard_notes if not guard_ok]
                reason = ";".join([*problems, *extra]) or "identity_mismatch"
                append_jsonl(
                    quarantine_path,
                    {"registration_number": reg, "reason": reason, "parsed": parsed, "fetched_at": utcnow()},
                )
                state["quarantined"] = sorted(set(state.get("quarantined", [])) | {reg})
                state["completed"] = sorted(set(state.get("completed", [])) | {reg})
                stats["quarantined"] += 1
                stats["fail_by_reason"][reason] = stats["fail_by_reason"].get(reason, 0) + 1
                save_checkpoint_atomic(checkpoint_path, state)
                bar.update(1, detail=f"{reg} quarantined:{reason}")
                save_stats()
                processed += 1
                live_snapshot(reg, f"quarantined:{reason}")
                logger.warning(f"{reg}: quarantined ({reason})")
                record_history(reg, "quarantined", reason, serial=serial_of(reg))
                continue

            # L1 saves — raw snapshot first (re-parseable without re-fetch).
            record = {
                "registration_number": reg,
                "serial_number": serial_of(reg),
                "fetched_at": utcnow(),
                "captcha_text": meta.get("captcha_text", ""),
                "captcha_attempts": meta.get("attempts", 1),
                "ms": meta.get("ms", {}),
                "total_ms": int((time.monotonic() - t0) * 1000),
                "guard_notes": guard_notes,
                "raw_cells": raw_cells,
                "parsed": parsed,
            }
            if guard_notes:
                stats["guard_noted"] = stats.get("guard_noted", 0) + 1
                logger.warning(f"{reg}: accepted with divergence {guard_notes}")
            raw_path = raw_dir / f"{reg}.json"
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            append_jsonl(
                out_jsonl,
                {
                    **parsed,
                    "serial_number": serial_of(reg),
                    "fetched_at": record["fetched_at"],
                    "guard_notes": guard_notes,
                },
            )

            if sync_cloud:
                pending_payloads.append(build_supabase_payload(parsed, record["fetched_at"], serial_of(reg)))
                if len(pending_payloads) >= sync_every:
                    flush_supabase_batch()
                    push_file_to_r2(out_jsonl, "dg-contacts/dg_contacts.jsonl")

            state["completed"] = sorted(set(state.get("completed", [])) | {reg})
            state["failed"].pop(reg, None)
            state.get("failed_terminal", {}).pop(reg, None)
            save_checkpoint_atomic(checkpoint_path, state)
            stats["done"] += 1
            elapsed = time.monotonic() - start_all
            rate = (stats["done"] + stats["failed"] + stats["quarantined"]) / elapsed if elapsed > 0 else 0
            bar.update(1, detail=f"{reg} ok {rate:.2f}/s")
            save_stats()
            processed += 1
            live_snapshot(reg, "saved")
            logger.info(f"{reg}: saved ({record['total_ms']}ms, captcha {meta.get('captcha_text', '')})")
            record_history(
                reg, "saved", ms=record["total_ms"], serial=serial_of(reg), captcha_attempts=meta.get("attempts", 1)
            )

    stats["finished_at"] = utcnow()
    if sync_cloud:
        flush_supabase_batch()
        retry_deferred()
        done_regs = [normalize_reg(r) for r in reg_ids if normalize_reg(r) in set(state.get("completed", []))]
        stats["cloud_snapshot"] = sync_cloud_snapshot(out_jsonl, raw_dir, done_regs)
    save_stats()
    if push_r2:
        paths = [out_jsonl, quarantine_path, stats_path, checkpoint_path]
        push_dg_to_r2([p for p in paths if p.exists()])
    status = "stopped" if stats.get("stopped") else "finished"
    write_live(
        status=status,
        event=f"run {status}",
        current_reg="",
        done=stats["done"],
        failed=stats["failed"],
        quarantined=stats["quarantined"],
    )
    logger.info(
        f"DG fetch {status}: done={stats['done']} failed={stats['failed']} "
        f"quarantined={stats['quarantined']} sb_upserted={stats.get('sb_upserted', 0)}"
    )
    return stats
