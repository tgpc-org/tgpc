"""
Core management logic for TGPC: restore, daily update orchestration, and
the photo upload pipeline.

Sync destinations live in tgpc.sync, enrichment in tgpc.enrichment, and
storage primitives in tgpc.storage — re-exported here so imports and test
patch targets (tgpc.manager.R2Client, tgpc.manager.BackupManager, ...)
keep working.
"""

import json
import os
import re
import subprocess  # noqa: F401  (patch target: tgpc.manager.subprocess used by tgpc.sync)
import requests
from pathlib import Path
from typing import Iterable
from collections import Counter

from supabase import create_client  # noqa: F401  (patch target: tgpc.manager.create_client)

from tgpc.utils import Config, BlockedError, setup_logging, load_credentials
from tgpc.scraper import Scraper, PharmacistRecord
from tgpc.progress import ProgressBar, Phase, heartbeat, step

# Storage primitives (R2Client, FileManager, BackupManager, DataIntegrityError,
# validate_rph_backup) — re-exported for backward compatibility.
from tgpc.storage import (  # noqa: F401
    R2Client,
    FileManager,
    BackupManager,
    DataIntegrityError,
    validate_rph_backup,
)

# Extracted subsystems, re-exported so callers can import them from either place.
from tgpc import sync as _sync  # noqa: F401
from tgpc import enrichment as _enrichment  # noqa: F401


logger = setup_logging("tgpc.manager")


class Manager:
    """Main management class."""

    def __init__(self):
        load_credentials()
        self.config = Config.load()
        self.file_manager = FileManager(self.config)
        self.backup_manager = BackupManager(self.config)
        self.scraper = Scraper()

    # ------------------------------------------------------------------
    # Client factories
    #
    # Tests patch tgpc.manager.create_client / tgpc.manager.Scraper /
    # tgpc.manager.R2Client; keeping construction behind these methods means
    # moved code still resolves the patched names through this module.
    # ------------------------------------------------------------------

    def _make_supabase_client(self, url=None, key=None):
        url = url if url is not None else os.environ.get("SUPABASE_URL")
        key = key if key is not None else os.environ.get("SUPABASE_SECRET_KEY")
        return create_client(url, key)

    def _make_r2_client(self, endpoint, access_key, secret_key):
        return R2Client(endpoint, access_key, secret_key)

    # ------------------------------------------------------------------
    # Update orchestration
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_exception_chain(error: BaseException) -> Iterable[BaseException]:
        seen = set()
        pending = [error]

        while pending:
            current = pending.pop()
            if current is None:
                continue

            current_id = id(current)
            if current_id in seen:
                continue
            seen.add(current_id)
            yield current

            for attr in ("original_error", "__cause__", "__context__"):
                nested = getattr(current, attr, None)
                if isinstance(nested, BaseException):
                    pending.append(nested)

    @classmethod
    def _is_source_unavailable_error(cls, error: BaseException) -> bool:
        for current in cls._iter_exception_chain(error):
            if isinstance(
                current,
                (requests.exceptions.Timeout, requests.exceptions.ConnectionError),
            ):
                return True

            # Catch urllib3 timeout errors (common in GitHub Actions)
            try:
                from urllib3.exceptions import (
                    ConnectTimeoutError,
                    MaxRetryError,
                    NewConnectionError,
                )

                if isinstance(current, (ConnectTimeoutError, MaxRetryError, NewConnectionError)):
                    return True
            except ImportError:
                pass

            if isinstance(current, requests.exceptions.HTTPError):
                status_code = getattr(getattr(current, "response", None), "status_code", None)
                if status_code == 429 or (status_code is not None and status_code >= 500):
                    return True

            if isinstance(current, BlockedError):
                return True

        return False

    @classmethod
    def _source_error_label(cls, error: BaseException) -> str:
        for current in cls._iter_exception_chain(error):
            if isinstance(current, requests.exceptions.RequestException):
                return type(current).__name__

            # Also handle urllib3 exceptions
            try:
                from urllib3.exceptions import (
                    ConnectTimeoutError,
                    MaxRetryError,
                    NewConnectionError,
                )

                if isinstance(current, (ConnectTimeoutError, MaxRetryError, NewConnectionError)):
                    return type(current).__name__
            except ImportError:
                pass

        return type(error).__name__

    def _write_update_outputs(self, **values) -> None:
        output_path = os.environ.get("GITHUB_OUTPUT")

        defaults = {
            "update_status": "",
            "source_error": "",
            "success": False,
            "total_records": 0,
            "new_records": 0,
            "removed_records": 0,
            "modified_records": 0,
            "duplicates_removed": 0,
            "integrity_score": 1.0,
            "new_details": [],
            "removed_details": [],
            "modified_details": [],
            "new_cat_stats": {},
            "rem_cat_stats": {},
            "mod_cat_stats": {},
            "blocked": False,
        }
        defaults.update(values)

        if output_path:
            with open(output_path, "a", encoding="utf-8") as f:
                for key, value in defaults.items():
                    if isinstance(value, (dict, list)):
                        serialized = json.dumps(value)
                    else:
                        serialized = str(value)
                    f.write(f"{key}={serialized}\n")

    def _restore_rph_from_backup(self) -> bool:
        """Restore data/rph.json from latest R2 backup if local file is missing or corrupted.

        Returns True if file exists after this call (either already existed or restored).
        """
        rph_path = Path(self.config.data_directory) / "rph.json"

        # Try to get Supabase count for validation
        # TGPC_ALLOW_SMALL_RPH=1 lets unit tests use tiny fixtures without R2.
        if os.environ.get("TGPC_ALLOW_SMALL_RPH") == "1":
            expected_min_records = 1
        else:
            expected_min_records = 1000  # production: 1-record stub is corrupt
        try:
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_SECRET_KEY")
            if url and key:
                supabase = self._make_supabase_client(url, key)
                result = supabase.table("rph").select("registration_number", count="exact", head=True).execute()
                if result.count and result.count > 1000:
                    expected_min_records = int(result.count * 0.5)  # expect at least 50% of Supabase
        except Exception:
            pass  # fallback to default minimum

        if rph_path.exists():
            try:
                with open(rph_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) >= expected_min_records:
                    logger.info(f"data/rph.json OK ({len(data)} records, expected >= {expected_min_records})")
                    return True
                logger.warning(
                    f"data/rph.json small ({len(data) if isinstance(data, list) else 'invalid'} records, "
                    f"expected >= {expected_min_records}) — restoring from R2..."
                )
            except Exception as e:
                logger.warning(f"data/rph.json failed validation ({e}) — restoring from R2...")
            # Treat as missing, fall through to R2 restore. NOTE: the local
            # file is intentionally NOT deleted here — it is only replaced
            # after a downloaded backup has been validated (see below).

        logger.warning("data/rph.json not found — attempting restore from R2 backup...")
        endpoint = self.backup_manager._r2_endpoint()
        access_key = os.environ.get("R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        if not endpoint or not access_key or not secret_key:
            logger.error("Cannot restore: missing CLOUDFLARE_ACCOUNT_ID or R2 credentials")
            return False

        client = self._make_r2_client(endpoint, access_key, secret_key)

        # List backups to find latest
        try:
            contents = client.list_objects("backups/")
        except Exception as e:
            logger.error(f"Cannot list R2 backups: {e}")
            return False

        if not contents:
            logger.error("No backups found in R2")
            return False

        # Newest first — but newest is not the same as usable. A failed or
        # truncated upload can be both newer *and* broken, and trusting it cost
        # the entire daily update while good backups sat right there. Try
        # candidates in recency order until one validates and is used.
        # (audit lead 8: restore trusted the newest backup by timestamp.)
        candidates = sorted(
            (c for c in contents if str(c.get("Key", "")).endswith(".json")),
            key=lambda c: c["LastModified"],
            reverse=True,
        )
        if not candidates:
            logger.error("No backup objects found in R2")
            return False

        rph_path.parent.mkdir(parents=True, exist_ok=True)
        rejected = []
        for candidate in candidates:
            backup_key = candidate["Key"]
            # Only restore if the R2 backup is meaningfully larger than local
            # (never overwrite a test stub with another stub).
            if candidate.get("Size", 0) < 100:
                logger.warning(f"R2 backup {backup_key} too small ({candidate.get('Size')} bytes) — skipping")
                rejected.append(backup_key)
                continue

            # Download to a temp path first — never overwrite the local file
            # with unvalidated remote content.
            tmp_path = rph_path.with_suffix(".restore_tmp")
            try:
                client.get_object(backup_key, tmp_path)
            except Exception as e:
                tmp_path.unlink(missing_ok=True)
                logger.warning(f"Failed to download {backup_key}: {e} — trying an older backup")
                rejected.append(backup_key)
                continue

            # Validate the downloaded backup before it replaces anything.
            try:
                validate_rph_backup(tmp_path, expected_min_records)
            except Exception as e:
                tmp_path.unlink(missing_ok=True)
                logger.warning(f"Backup {backup_key} failed validation ({e}) — trying an older backup")
                rejected.append(backup_key)
                continue

            tmp_path.replace(rph_path)
            size = rph_path.stat().st_size
            logger.info(f"Restored data/rph.json from {backup_key} ({size} bytes)")
            return True

        logger.error(f"No R2 backup passed validation ({len(rejected)} rejected) — local file left untouched")
        return False

    def run_daily_update(self, force: bool = False):
        """Execute daily update workflow."""
        logger.info("Starting daily update...")

        # 0a. Restore rph.json from backup if missing (safety against accidental deletion)
        with Phase("Restore rph.json from backup", 1, 5):
            with heartbeat("Restoring rph.json from R2 backup"):
                restore_ok = self._restore_rph_from_backup()
            if not restore_ok:
                logger.error("data/rph.json is missing or invalid and restore failed — aborting update.")
                self._write_update_outputs(
                    update_status="restore_failed",
                    success=False,
                    total_records=0,
                )
                return "restore_failed"

        # 0b. Health check - abort if blocked
        with Phase("Health check", 2, 5) as p:
            with heartbeat("Checking source availability"):
                healthy = self.scraper.health_check()
            if not healthy:
                p.fail()
                logger.error("Health check failed - connection is blocked. Aborting to avoid wasted time.")
                self._write_update_outputs(
                    update_status="blocked",
                    success=False,
                    blocked=True,
                    total_records=len(self.file_manager.load()),
                )
                return "blocked"

        # 1. Backup existing
        with Phase("Backup existing data", 3, 5):
            rph_path = Path(self.config.data_directory) / "rph.json"
            with heartbeat("Uploading backup to R2"):
                self.backup_manager.create(rph_path)
            existing_records = self.file_manager.load()

        # 2. Scrape fresh data
        with Phase("Scrape fresh data", 4, 5) as p:
            with heartbeat("Extracting basic records"):
                try:
                    fresh_records = self.scraper.extract_basic_records()
                except Exception as e:
                    if self._is_source_unavailable_error(e):
                        p.fail()
                        source_error = self._source_error_label(e)
                        logger.warning(
                            "TGPC source is temporarily unavailable (%s). Preserving existing data and skipping sync.",
                            source_error,
                        )
                        self._write_update_outputs(
                            update_status="source_unavailable",
                            source_error=source_error,
                            success=False,
                            total_records=len(existing_records),
                        )
                        return "source_unavailable"
                    raise

        if not fresh_records:
            logger.error("No records extracted, aborting update")
            self._write_update_outputs(
                update_status="empty_scrape",
                success=False,
                total_records=len(existing_records),
            )
            return "empty_scrape"

        # Safety Check: Prevent massive data loss (--force overrides both caps)
        if not force and existing_records and len(fresh_records) < len(existing_records) * 0.9:
            logger.error(
                f"Safety Alert: New count ({len(fresh_records)}) < 90% of existing ({len(existing_records)}). Aborting."
            )
            self._write_update_outputs(
                update_status="safety_abort",
                success=False,
                total_records=len(existing_records),
            )
            return "safety_abort"

        # 3. Validate & Save
        with Phase("Validate & save", 5, 5):
            # Simple deduplication by registration number
            unique_records = {r.registration_number: r for r in fresh_records}.values()
            sorted_records = sorted(unique_records, key=lambda r: r.serial_number or 0)

            # Calculate stats
            existing_map = {r.registration_number: r for r in existing_records}
            current_map = {r.registration_number: r for r in sorted_records}

            existing_ids = set(existing_map.keys())
            current_ids = set(current_map.keys())

            new_ids = current_ids - existing_ids
            removed_ids = existing_ids - current_ids
            common_ids = current_ids & existing_ids

            new_count = len(new_ids)
            removed_count = len(removed_ids)
            total_count = len(sorted_records)
            duplicates = len(fresh_records) - len(sorted_records)

            def detail_sort_key(record: PharmacistRecord):
                return (
                    record.serial_number is None,
                    record.serial_number if record.serial_number is not None else 0,
                    record.registration_number,
                )

            def format_detail(record: PharmacistRecord) -> str:
                return f"{record.registration_number} - {record.name} ({record.category})"

            sorted_new_ids = sorted(new_ids, key=lambda rid: detail_sort_key(current_map[rid]))
            sorted_removed_ids = sorted(removed_ids, key=lambda rid: detail_sort_key(existing_map[rid]))

            # Detailed changes
            new_details = [format_detail(current_map[rid]) for rid in sorted_new_ids]
            removed_details = [format_detail(existing_map[rid]) for rid in sorted_removed_ids]

            modified_ids = sorted(
                [rid for rid in common_ids if existing_map[rid] != current_map[rid]],
                key=lambda rid: detail_sort_key(current_map[rid]),
            )
            modified_count = len(modified_ids)
            modified_details = [format_detail(current_map[rid]) for rid in modified_ids]

            # Category Statistics
            def get_cat_stats(ids, mapping):
                counts = Counter(mapping[i].category for i in ids)
                return dict(sorted(counts.items()))

            new_cat_stats = get_cat_stats(sorted_new_ids, current_map)
            rem_cat_stats = get_cat_stats(sorted_removed_ids, existing_map)
            mod_cat_stats = get_cat_stats(modified_ids, current_map)  # Use modified_ids, NOT common_ids

            # Safety abort: only block large DROPS, not legitimate growth
            # Allow any amount of growth (new records), but abort if many records removed
            if not force and len(removed_ids) > 100:
                logger.error(
                    f"SAFETY ABORT: {len(removed_ids)} records removed (limit 100) — "
                    f"possible data loss. Run --force to override."
                )
                self._write_update_outputs(
                    update_status="safety_abort",
                    success=False,
                    total_records=len(existing_records),
                )
                return "safety_abort"

            self.file_manager.save(list(sorted_records))
            self._last_new_regs = new_ids
            self._last_modified_regs = modified_ids
            self._last_removed_regs = removed_ids

            logger.info(
                "Update complete. Total: %d, 🌱 NEW: %d, 🌀 CHANGES: %d, ❌ REMOVALS: %d",
                total_count,
                new_count,
                modified_count,
                removed_count,
            )

            self._last_update_details = {
                "new_details": new_details,
                "modified_details": modified_details,
                "removed_details": removed_details,
                "new_cat_stats": new_cat_stats,
                "rem_cat_stats": rem_cat_stats,
                "mod_cat_stats": mod_cat_stats,
                "total_records": total_count,
            }

            if os.environ.get("GITHUB_OUTPUT"):
                details_path = Path(self.file_manager.data_dir) / "update_details.json"
                with open(details_path, "w", encoding="utf-8") as f:
                    json.dump(self._last_update_details, f, indent=2, ensure_ascii=False)

            self._write_update_outputs(
                update_status="updated",
                success=True,
                total_records=total_count,
                new_records=new_count,
                removed_records=removed_count,
                modified_records=modified_count,
                duplicates_removed=duplicates,
                integrity_score=1.0,
                new_details=new_details,
                removed_details=removed_details,
                modified_details=modified_details,
                new_cat_stats=new_cat_stats,
                rem_cat_stats=rem_cat_stats,
                mod_cat_stats=mod_cat_stats,
            )
            return "updated"

    # ------------------------------------------------------------------
    # Sync destinations — bodies in tgpc/sync.py
    # ------------------------------------------------------------------

    def sync_to_supabase(self, delta_records=None):
        """Sync data to Supabase. If delta_records provided, only upsert those.

        Returns True on success, False on failure so callers can propagate it.
        """
        return _sync.sync_to_supabase(self, delta_records)

    def _delete_supabase_orphans(self, supabase, local_ids: set) -> bool:
        """Delete remote rows whose registration_number is not in local_ids.

        Only called after an upsert-only repair still leaves remote ahead of
        local, so the count drift actually heals instead of logging CRITICAL
        on every run forever.
        """
        return _sync.delete_supabase_orphans(supabase, local_ids)

    def delete_removed_from_supabase(self, removed_ids: set) -> bool:
        """Delete removed records from Supabase."""
        return _sync.delete_removed_from_supabase(self, removed_ids)

    def sync_to_supabase_storage(self):
        """Upload rph.json to Supabase Storage (tgpc bucket)."""
        return _sync.sync_to_supabase_storage(self)

    def sync_to_r2(self):
        """Sync rph.json to Cloudflare R2. Photos are uploaded during enrichment.

        Returns True on success, False on failure so callers can propagate it.
        """
        return _sync.sync_to_r2(self)

    def sync_to_gdrive(self):
        """Sync rph.json to Google Drive via rclone.

        Returns True on success, False on failure so callers can propagate it.
        """
        return _sync.sync_to_gdrive(self)

    def sync_to_release(self):
        """Encrypt rph.json and upload to GitHub Release.

        Returns True on success, False on failure so callers can propagate it.
        """
        return _sync.sync_to_release(self)

    def sync_to_email(self):
        """Send update report via Resend email.

        Returns True on success, False on failure so callers can propagate it.
        """
        return _sync.sync_to_email(self)

    def email_record_list(self, reg_ids) -> bool:
        """Email a record list using the DEFAULT make-scrape report format.

        Standing rule: every records email uses sync_to_email() — no custom
        formats. Builds _last_update_details from the given registration IDs
        (as NEW) and delegates to sync_to_email().
        """
        return _sync.email_record_list(self, reg_ids)

    # ------------------------------------------------------------------
    # Photo upload pipeline (kept here — tests patch these Manager methods)
    # ------------------------------------------------------------------

    def _get_r2_env(self):
        """Build env dict with R2 credentials for aws s3api commands."""
        access_key = os.environ.get("R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        env = os.environ.copy()
        if access_key:
            env["AWS_ACCESS_KEY_ID"] = access_key
        if secret_key:
            env["AWS_SECRET_ACCESS_KEY"] = secret_key
        return env

    def _get_r2_endpoint(self):
        """Build R2 endpoint URL."""
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        return f"https://{account_id}.r2.cloudflarestorage.com" if account_id else None

    def upload_and_verify_photo(self, local_path, r2_key, max_retries=5):
        """Upload a photo to R2 with aggressive immediate retries. Returns True on success."""
        import time

        for attempt in range(1, max_retries + 1):
            step(f"uploading photo to R2 (attempt {attempt}/{max_retries})")
            uploaded = self._upload_photo_to_r2(local_path, r2_key)
            if not uploaded:
                if attempt < max_retries:
                    delay = 2**attempt  # 2s, 4s, 8s, 16s
                    logger.warning(
                        f"Upload attempt {attempt}/{max_retries} failed for {r2_key}, retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                return False

            verified = self._verify_photo_on_r2(r2_key, local_path.stat().st_size)
            if verified:
                step("photo verified on R2")
                return True

            if attempt < max_retries:
                delay = 2**attempt
                logger.warning(
                    f"Verification attempt {attempt}/{max_retries} failed for {r2_key}, retrying in {delay}s..."
                )
                time.sleep(delay)

        return False

    def _upload_photo_to_r2(self, local_path, r2_key):
        """Upload a single photo to R2. Returns True on success."""
        endpoint = self._get_r2_endpoint()
        if not endpoint:
            logger.error("Missing CLOUDFLARE_ACCOUNT_ID for R2 upload")
            return False

        access_key = os.environ.get("R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        if not all([access_key, secret_key]):
            logger.error("Missing R2 credentials")
            return False

        client = self._make_r2_client(endpoint, access_key, secret_key)

        try:
            with open(local_path, "rb") as f:
                client.client.put_object(
                    Bucket=R2Client.BUCKET,
                    Key=r2_key,
                    Body=f,
                    ContentType="image/webp",
                    CacheControl="public, max-age=86400",
                )
            return True
        except Exception as e:
            logger.error(f"R2 upload error for {r2_key}: {e}")
            return False

    def _verify_photo_on_r2(self, r2_key, expected_size):
        """Cloud-only verification: confirm file exists on R2 with correct size."""
        endpoint = self._get_r2_endpoint()
        if not endpoint:
            return False

        access_key = os.environ.get("R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        if not all([access_key, secret_key]):
            return False

        client = self._make_r2_client(endpoint, access_key, secret_key)

        try:
            head = client.head_object(r2_key)
            actual_size = head.get("ContentLength", -1)
            if actual_size == expected_size:
                return True
            logger.warning(f"R2 verification size mismatch for {r2_key}: expected {expected_size}, got {actual_size}")
            return False
        except Exception as e:
            logger.error(f"R2 verification error for {r2_key}: {e}")
            return False

    def retry_photos(self):
        """Retry uploading all local WebP files to R2. Resolves failures from the same session."""
        photos_dir = self.file_manager.data_dir / "webp"
        if not photos_dir.is_dir():
            logger.info("No data/webp/ directory — nothing to retry")
            return

        webp_files = sorted(photos_dir.glob("*.webp"))
        if not webp_files:
            logger.info("No .webp files in data/webp/ — nothing to retry")
            return

        logger.info(f"Retrying {len(webp_files)} photos to R2...")
        succeeded = 0
        failed = []

        with ProgressBar(total=len(webp_files), label="Retrying photos to R2") as bar:
            for photo_file in webp_files:
                reg_no = photo_file.stem
                safe_reg = re.sub(r"[^A-Za-z0-9._-]", "", reg_no)
                r2_key = f"photos/{safe_reg}.webp"

                if self.upload_and_verify_photo(photo_file, r2_key):
                    photo_file.unlink()
                    succeeded += 1
                    logger.info(f"Retry OK + local deleted: {reg_no}")
                else:
                    failed.append(reg_no)
                    logger.error(f"Retry FAILED: {reg_no} — local file kept")
                bar.update(1, detail=reg_no)

        logger.info(f"Retry complete: {succeeded} uploaded, {len(failed)} failed")
        if failed:
            logger.critical(f"Still failed: {', '.join(failed)}")

    # ------------------------------------------------------------------
    # Enrichment — bodies in tgpc/enrichment.py
    # ------------------------------------------------------------------

    def run_enrichment(self, start: int = 1, stop: int = None):
        """Run enrichment for serial number range.

        Args:
            start: Start from serial number (default: 1)
            stop: Stop at serial number (default: all)
        """
        return _enrichment.run_enrichment(self, start, stop)

    def enrich_new_records(self, force: bool = False):
        """Auto-enrich records that need enrichment.

        Args:
            force: If False, abort when >1000 candidate records (safety guard against
                   corrupt/missing rph.json causing full re-enrichment).
                   If True, enrich all records missing enrichment data.
        """
        return _enrichment.enrich_new_records(self, force)

    def _process_records_sequential(
        self, pending_records, rph_lookup, img_dir, ip_rotation_interval=500, supabase=None
    ):
        """Process records sequentially."""
        return _enrichment.process_records_sequential(
            self, pending_records, rph_lookup, img_dir, ip_rotation_interval, supabase
        )
