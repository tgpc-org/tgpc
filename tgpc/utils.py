"""
Core utilities for TGPC system.
Combines configuration, logging, credentials, and exception handling.
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tgpc.progress import BarHandler

# --- Exceptions ---


class TGPCError(Exception):
    """Base exception for all TGPC-related errors."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(message)
        self.original_error = original_error


class BlockedError(TGPCError):
    """Raised when the source responds with a block/waf page (200 with WAF
    content, or an unexpected redirect) rather than transport or HTTP errors.
    Distinguishable from parser failures so callers can treat a genuine block
    as a recoverable 'source unavailable' (CODE_REVIEW.md M9)."""


# --- Configuration ---


@dataclass
class Config:
    """Configuration for TGPC system."""

    # API Settings
    base_url: str = "https://www.pharmacycouncil.telangana.gov.in"
    connect_timeout: int = 20
    read_timeout: int = 180
    max_retries: int = 3
    proxy_url: Optional[str] = None

    # Optional SHA-256 fingerprint (hex, colons allowed) of the TGPC host
    # certificate. Empty (the default) preserves current behaviour: chain
    # validation stays on and only the hostname match is relaxed, because the
    # source certificate is issued for a different name. Set it to pin the
    # exact certificate and close the remaining MITM gap (audit F5/F10).
    tls_cert_sha256: Optional[str] = None

    # Rate Limiting
    min_delay: float = 3.0
    max_delay: float = 8.0
    long_break_after: int = 100
    long_break_duration: int = 60

    # Storage
    data_directory: str = "data"
    enrichment_directory: str = "data"
    r2_public_base: str = "https://pub-4591c8c5282040459ade2ed1e5e3d5be.r2.dev"

    # User Agent
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )

    @classmethod
    def load(cls) -> "Config":
        """Load configuration."""
        proxy_url = (
            os.environ.get("TGPC_PROXY_URL") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
        )
        enrichment_dir = os.environ.get("TGPC_ENRICHMENT_DIR", "data")
        tls_cert_sha256 = (os.environ.get("TGPC_TLS_CERT_SHA256") or "").strip() or None

        return cls(
            proxy_url=proxy_url,
            enrichment_directory=enrichment_dir,
            r2_public_base=os.environ.get("TGPC_R2_PUBLIC_BASE", Config.r2_public_base),
            tls_cert_sha256=tls_cert_sha256,
        )


# --- Logging ---


def setup_logging(name: str = "tgpc") -> logging.Logger:
    """Set up minimal logging to stderr (keeps progress bars on stdout clean)."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = BarHandler(sys.stderr)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger


# --- Credentials ---

KEYCHAIN_SERVICE = "tgpc"

CREDENTIAL_KEYS = [
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_PAT",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "TGPC_R2_DG_BUCKET",  # private bucket for DG PII — never the public one
    "RCLONE_GDRIVE_CONFIG",
    "RESEND_API_KEY",
    "NOTIFICATION_EMAIL",
    "RELEASE_PASSWORD",
]

_creds_logger = setup_logging("tgpc.credentials")


def _get_keychain(key: str) -> str | None:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _set_keychain(key: str, value: str) -> None:
    existing = _get_keychain(key)
    if existing:
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key],
            capture_output=True,
            check=True,
        )
    subprocess.run(
        ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key, "-w", value, "-U"],
        capture_output=True,
        check=True,
    )


def _load_from_files():
    candidates = [
        Path.home() / ".config" / "tgpc" / "creds.sh",
        Path(__file__).parent.parent / "tgpc-creds.sh",
    ]
    for creds_file in candidates:
        if not creds_file.exists():
            continue
        loaded = 0
        try:
            with open(creds_file, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped.startswith("export "):
                        continue
                    # Per-line handling: one malformed line must not abort
                    # the rest of the file.
                    try:
                        var, value = stripped[7:].split("=", 1)
                    except ValueError:
                        _creds_logger.warning("Skipping malformed line in %s: %s", creds_file, stripped)
                        continue
                    value = value.strip("\"'")
                    if not os.environ.get(var):
                        os.environ[var] = value
                        loaded += 1
            if loaded:
                _creds_logger.info("Loaded %d credential(s) from %s", loaded, creds_file)
        except Exception as e:
            _creds_logger.warning("Could not load %s: %s", creds_file, e)


def load_credentials():
    """Load credentials: env vars → macOS Keychain → file fallback."""
    _load_from_files()
    for key in CREDENTIAL_KEYS:
        if os.environ.get(key):
            continue
        val = _get_keychain(key)
        if val is not None:
            os.environ[key] = val
