"""
Core scraping logic for TGPC system.
Simple, clean scraper for local use only.
"""

import time
import random
import re
import ssl
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from tgpc.progress import step
from tgpc.utils import Config, BlockedError, setup_logging

logger = setup_logging("tgpc.scraper")

BLOCKED_MARKERS = (
    "access denied",
    "forbidden",
    "captcha",
    "blocked",
    "suspicious",
    "security check",
    "unusual traffic",
)


def _is_blocked(content: str) -> bool:
    """True if a (successful HTTP) response body looks like a block/WAF page
    rather than the real pharmacy council content (CODE_REVIEW.md M9)."""
    lowered = content.lower()
    return any(marker in lowered for marker in BLOCKED_MARKERS)


class _TGPCTLSAdapter(HTTPAdapter):
    """HTTPS adapter used only for the TGPC host.

    Validates the server certificate *chain* (`CERT_REQUIRED`) so MITM
    against a tampered cert is still detected, but skips the hostname
    match (`check_hostname = False`) because the TGPC certificate is
    issued for a different name (CODE_REVIEW.md C4).
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        kwargs["ssl_context"] = ctx
        kwargs["assert_hostname"] = False
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        kwargs["ssl_context"] = ctx
        kwargs["assert_hostname"] = False
        return super().proxy_manager_for(*args, **kwargs)


# --- Models ---


@dataclass
class PharmacistRecord:
    registration_number: str
    name: str
    father_name: str
    category: str
    serial_number: Optional[int] = None

    gender: Optional[str] = None
    validity_date: Optional[str] = None
    status: Optional[str] = None

    education: Optional[List[Dict[str, str]]] = None
    work_experience: Optional[Dict[str, str]] = None
    photo_url: Optional[str] = None

    def to_dict(self):
        """Convert to dictionary, strictly maintaining the 5-field schema for rph.json."""
        return {
            "registration_number": self.registration_number,
            "name": self.name,
            "father_name": self.father_name,
            "category": self.category,
            "serial_number": self.serial_number,
        }

    def to_detailed_dict(self):
        """Convert to detailed dictionary for individual enrichment JSON files."""
        return {
            "serial_number": self.serial_number,
            "registration_number": self.registration_number,
            "name": self.name,
            "father_name": self.father_name,
            "gender": self.gender or "",
            "validity_date": self.validity_date or "",
            "category": self.category,
            "status": self.status or "",
            "education": self.education or [],
            "work_experience": self.work_experience or {"Address": "", "State": "", "District": "", "Pin code": ""},
            "photo_url": self.photo_url or "",
        }


# --- Rate Limiter ---


class RateLimiter:
    def __init__(self, config: Config):
        self.min_delay = config.min_delay
        self.max_delay = config.max_delay
        self.current_delay = config.min_delay
        self.consecutive_failures = 0

    def wait(self):
        delay = self.current_delay * random.uniform(0.8, 1.2)
        time.sleep(delay)

    def record_result(self, success: bool):
        if success:
            self.consecutive_failures = 0
            self.current_delay = max(self.min_delay, self.current_delay * 0.9)
        else:
            self.consecutive_failures += 1
            self.current_delay = min(self.max_delay, self.current_delay * 1.5)


# --- Scraper ---


class Scraper:
    def __init__(self):
        self.config = Config.load()
        self.rate_limiter = RateLimiter(self.config)

        # Browser-like session with connection pooling.
        #
        # TLS policy (CODE_REVIEW.md C4): certificate *chain* validation stays
        # enabled for every request. Only the hostname match is relaxed, and
        # only for the TGPC host, whose certificate is issued for a different
        # name. Every other host (including photo CDNs) is fully verified.
        self.session = requests.Session()
        tgpc_tls = _TGPCTLSAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=3,
        )
        self.session.mount("https://www.pharmacycouncil.telangana.gov.in", tgpc_tls)
        self.session.mount("https://pharmacycouncil.telangana.gov.in", tgpc_tls)

        # Fully verifying adapter for every other host
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,  # Number of connection pools to cache
            pool_maxsize=10,  # Maximum number of connections in pool
            max_retries=3,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self.session.headers.update(
            {
                "User-Agent": random.choice(
                    [
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    ]
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Referer": self.config.base_url,
            }
        )

        self.urls = {
            "total": f"{self.config.base_url}/pharmacy/srchpharmacisttotal",
            "search": f"{self.config.base_url}/pharmacy/getsearchpharmacist",
        }

        logger.info("Simple scraper initialized - direct connection only")

    def health_check(self, timeout: int | None = None, max_retries: int = 3) -> bool:
        """Quick health check to detect if connection is blocked.

        Args:
            timeout: Custom timeout for the request. Uses config.read_timeout if None.
            max_retries: Number of retry attempts with exponential backoff.
        """
        for attempt in range(max_retries):
            try:
                t = timeout if timeout is not None else self.config.read_timeout
                response = self.session.get(self.urls["total"], timeout=(self.config.connect_timeout, t))

                if response.status_code != 200:
                    logger.warning(
                        f"Health check failed (attempt {attempt + 1}/{max_retries}): status {response.status_code}"
                    )
                    if attempt < max_retries - 1:
                        import time

                        wait_time = 2**attempt  # exponential backoff: 1s, 2s, 4s
                        logger.info(f"Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    return False

                if _is_blocked(response.text):
                    marker = next(m for m in BLOCKED_MARKERS if m in response.text.lower())
                    logger.warning(
                        f"Health check failed (attempt {attempt + 1}/{max_retries}): blocked indicator '{marker}'"
                    )
                    if attempt < max_retries - 1:
                        import time

                        wait_time = 2**attempt
                        logger.info(f"Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    return False

                logger.info("Health check passed - connection OK")
                return True

            except Exception as e:
                logger.warning(f"Health check failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import time

                    wait_time = 2**attempt
                    logger.info(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                return False

        return False

    @staticmethod
    def _normalize_header(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    @staticmethod
    def _cell_text(cell) -> str:
        return " ".join(cell.stripped_strings)

    @staticmethod
    def _parse_date_value(value: str) -> Optional[str]:
        """Return date value as-is from source (no conversion)."""
        if not value or value.strip() == "-" or value.strip() == "":
            return None
        return value.strip()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Simple direct request - no proxies, no Tor."""
        self.rate_limiter.wait()
        timeout = (self.config.connect_timeout, self.config.read_timeout)

        try:
            response = self.session.request(method, url, timeout=timeout, **kwargs)
            response.raise_for_status()

            # Detect block/WAF pages often served with a 200 status.
            # raise_for_status() above already turns 4xx/5xx into HTTPError;
            # here we distinguish a genuine block (recoverable as
            # "source unavailable") from a parser bug (CODE_REVIEW.md M9).
            if _is_blocked(response.text):
                marker = next(m for m in BLOCKED_MARKERS if m in response.text.lower())
                raise BlockedError(f"Blocked response from source (marker: '{marker}')")

            self.rate_limiter.record_result(True)
            return response

        except Exception as e:
            self.rate_limiter.record_result(False)
            raise e

    def extract_basic_records(self) -> List[PharmacistRecord]:
        logger.info("Extracting basic records...")
        response = self._request("GET", self.urls["total"])
        soup = BeautifulSoup(response.content, "html.parser")

        records = []
        table = soup.find("table", attrs={"id": "tablesorter-demo"}) or soup.find("table")

        if not table:
            return []

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            try:
                records.append(
                    PharmacistRecord(
                        serial_number=int(cells[0].get_text(strip=True))
                        if cells[0].get_text(strip=True).isdigit()
                        else None,
                        registration_number=cells[1].get_text(strip=True),
                        name=cells[2].get_text(strip=True),
                        father_name=cells[3].get_text(strip=True),
                        category=cells[4].get_text(strip=True),
                    )
                )
            except Exception:
                continue

        logger.info(f"Extracted {len(records)} records")
        return records

    def extract_detailed_info(self, reg_no: str, img_dir: Path = None) -> Optional[PharmacistRecord]:
        try:
            logger.info(f"Enriching {reg_no}...")
            step(f"searching {reg_no}")
            response = self._request(
                "POST",
                self.urls["search"],
                data={"registration_no": reg_no, "submit": "Submit"},
            )

            if "No Records Found" in response.text:
                return None

            soup = BeautifulSoup(response.content, "html.parser")
            tables = soup.find_all("table")

            if not tables:
                return None

            record = PharmacistRecord(registration_number=reg_no, name="", father_name="", category="")

            basic_headers = []
            basic_values = {}

            img = soup.find("img", id=re.compile(r"imgPhoto", re.I))
            if not img:
                info_table = next(
                    (
                        table
                        for table in tables
                        if "registration no"
                        in [self._normalize_header(th.get_text(" ", strip=True)) for th in table.find_all("th")]
                    ),
                    None,
                )
                if info_table:
                    img = info_table.find("img")

            if img and img.get("src") and img_dir:
                src = img["src"]
                import base64
                from io import BytesIO

                step(f"downloading photo for {reg_no}")
                image_bytes = None
                if "base64" in src:
                    if src.startswith("data:"):
                        base64_data = src.split(",")[-1]
                        try:
                            image_bytes = base64.b64decode(base64_data)
                        except Exception:
                            pass
                elif src.startswith("/") or src.startswith("http"):
                    try:
                        photo_url = src if src.startswith("http") else f"{self.config.base_url}{src}"
                        photo_response = self._request("GET", photo_url)
                        if photo_response.status_code == 200:
                            image_bytes = photo_response.content
                    except Exception as e:
                        logger.warning(f"Failed to download photo from {src}: {e}")

                if image_bytes and len(image_bytes) > 100:
                    step(f"processing photo for {reg_no}")
                    try:
                        from PIL import Image, ImageOps

                        im = Image.open(BytesIO(image_bytes))

                        # Animated? Grab first frame only
                        if getattr(im, "is_animated", False):
                            im.seek(0)

                        # Apply EXIF orientation (fixes rotated phone photos)
                        try:
                            im = ImageOps.exif_transpose(im)
                        except Exception:
                            pass

                        # Flatten alpha onto white background for any mode with alpha
                        if im.mode in ("RGBA", "LA", "PA"):
                            bg = Image.new("RGB", im.size, (255, 255, 255))
                            if im.mode == "LA":
                                im = im.convert("RGBA")
                            elif im.mode == "PA":
                                im = im.convert("RGBA")
                            bg.paste(im, mask=im.split()[3])
                            im.close()
                            im = bg
                        elif im.mode == "CMYK":
                            im = im.convert("RGB")
                        elif im.mode == "P":
                            im = im.convert("RGBA")
                            bg = Image.new("RGB", im.size, (255, 255, 255))
                            bg.paste(im, mask=im.split()[3])
                            im.close()
                            im = bg
                        elif im.mode == "L":
                            im = im.convert("RGB")
                        elif im.mode not in ("RGB",):
                            try:
                                im = im.convert("RGB")
                            except Exception:
                                im = im.convert("RGB")

                        w, h = im.size
                        if w < 1 or h < 1:
                            logger.warning(f"Degenerate image {reg_no}: {w}x{h}")
                        else:
                            if w > 413 or h > 531:
                                ratio = min(413 / w, 531 / h)
                                im = im.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                            photo_path = img_dir / f"{reg_no}.webp"
                            im.save(photo_path, "WEBP", quality=85)
                            logger.info(f"Saved photo as WebP: {photo_path.name}")
                        im.close()
                    except Exception as e:
                        logger.warning(f"Failed to process photo for {reg_no}: {e}")

            for table in tables:
                headers = [self._normalize_header(th.get_text(" ", strip=True)) for th in table.find_all("th")]
                if "registration no" in headers and "name" in headers:
                    data_row = next(
                        (row for row in table.find_all("tr") if row.find_all("td")),
                        None,
                    )
                    if data_row:
                        cells = [self._cell_text(cell) for cell in data_row.find_all("td")]
                        basic_headers = headers
                        basic_values = {basic_headers[i]: cells[i] for i in range(min(len(basic_headers), len(cells)))}
                    break

            if basic_values:
                record.registration_number = basic_values.get("registration no") or reg_no
                record.name = basic_values.get("name", "")
                record.father_name = basic_values.get("father name", "")
                record.gender = basic_values.get("gender", "")
                record.category = basic_values.get("category", "")
                record.status = basic_values.get("status", "")
                # Try multiple possible header names for validity
                validity_date = None
                for key in ["validity", "valid upto", "valid up to", "validity date"]:
                    validity_date = self._parse_date_value(basic_values.get(key, ""))
                    if validity_date:
                        record.validity_date = validity_date
                        break

            if not record.name:
                return None

            main_text = soup.get_text()
            if not record.validity_date:
                validity_match = re.search(
                    r"Valid\s*(?:Upto|Up\s*to)\s*[:\-]?\s*([0-9]{2}(?:[-/][0-9]{2}(?:[-/][0-9]{4})|-[A-Za-z]{3}-[0-9]{4}))",
                    main_text,
                    re.I,
                )
                if validity_match:
                    parsed_date = self._parse_date_value(validity_match.group(1).replace("/", "-"))
                    if parsed_date:
                        record.validity_date = parsed_date

            for table in tables:
                headers = [self._normalize_header(th.get_text(" ", strip=True)) for th in table.find_all("th")]
                if any("qualification" in h for h in headers) or (
                    "category" in headers and any("board/university" in h or "university" in h for h in headers)
                ):
                    edu_list = []
                    rows = table.find_all("tr")[1:]
                    for row in rows:
                        cols = [self._cell_text(cell) for cell in row.find_all(["th", "td"])]
                        if len(cols) >= 2:
                            row_map = {headers[i]: cols[i] for i in range(min(len(headers), len(cols)))}
                            education = {
                                "Category": row_map.get("category") or row_map.get("qualification", ""),
                                "Board/University": row_map.get("board/university") or row_map.get("university", ""),
                                "College Name": row_map.get("college name", ""),
                                "College Address": row_map.get("college address", ""),
                                "From": row_map.get("from", ""),
                                "To": row_map.get("to", ""),
                                "HT No": row_map.get("ht no", ""),
                            }
                            edu_list.append(education)
                    record.education = edu_list

            record.work_experience = {
                "Address": "",
                "State": "",
                "District": "",
                "Pin code": "",
            }

            for table in tables:
                headers = [self._normalize_header(th.get_text(" ", strip=True)) for th in table.find_all("th")]
                if "address" in headers and "state" in headers and "district" in headers:
                    data_row = next(
                        (row for row in table.find_all("tr")[1:] if row.find_all("td")),
                        None,
                    )
                    if not data_row:
                        continue

                    cols = [self._cell_text(cell) for cell in data_row.find_all("td")]
                    row_map = {headers[i]: cols[i] for i in range(min(len(headers), len(cols)))}
                    work_info = {
                        "Address": row_map.get("address", ""),
                        "State": row_map.get("state", ""),
                        "District": row_map.get("district", ""),
                        "Pin code": row_map.get("pin code", ""),
                    }
                    record.work_experience = work_info
                    break

            return record

        except Exception as e:
            logger.error(f"Failed to extract details for {reg_no}: {e}")
            return None
