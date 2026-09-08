"""
Core management logic for TGPC system.
Handles file storage, backups, daily updates, and cloud sync.
"""

import json
import shutil
import os
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Iterable
from collections import Counter

from supabase import create_client

from tgpc.utils import Config, BlockedError, setup_logging, load_credentials
from tgpc.scraper import Scraper, PharmacistRecord
from tgpc.progress import ProgressBar, Phase, heartbeat, step


logger = setup_logging("tgpc.manager")


class DataIntegrityError(RuntimeError):
    """Raised when scraped detail data does not match the requested record."""


class FileManager:
    """Handles local file storage."""

    def __init__(self, config: Config):
        self.data_dir = Path(config.data_directory)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save(self, records: List[PharmacistRecord], filename: str = "rph.json") -> Path:
        """Save records to JSON atomically."""
        path = self.data_dir / filename
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        data = [r.to_dict() for r in records]
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        # Validate tmp before rename
        with open(tmp_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if len(loaded) < 1000 and len(records) >= 1000:
                raise ValueError(f"Tmp validation failed: {len(loaded)} < 1000")
        tmp_path.replace(path)
        logger.info(f"Saved {len(records)} records to {path}")
        return path

    def load(self, filename: str = "rph.json") -> List[PharmacistRecord]:
        """Load records from JSON."""
        path = self.data_dir / filename
        if not path.exists():
            return []

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return [PharmacistRecord(**d) for d in data]


class BackupManager:
    """Handles secure backups stored in R2."""

    def __init__(self, config: Config):
        self.config = config

    def _r2_env(self):
        env = os.environ.copy()
        env["AWS_ACCESS_KEY_ID"] = os.environ.get("R2_ACCESS_KEY_ID", "")
        env["AWS_SECRET_ACCESS_KEY"] = os.environ.get("R2_SECRET_ACCESS_KEY", "")
        return env

    def _r2_endpoint(self):
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        return f"https://{account_id}.r2.cloudflarestorage.com" if account_id else None

    def _upload_to_r2(self, local_path: Path) -> bool:
        endpoint = self._r2_endpoint()
        env = self._r2_env()
        if not endpoint:
            logger.warning("Missing CLOUDFLARE_ACCOUNT_ID — skipping R2 backup upload")
            return False

        r2_key = f"backups/{local_path.name}"
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
                r2_key,
                "--body",
                str(local_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if result.returncode != 0:
            logger.warning(f"R2 backup upload failed for {r2_key}: {result.stderr.strip()}")
            return False

        logger.info(f"Backup uploaded to R2: {r2_key}")

        result = subprocess.run(
            [
                "aws",
                "s3api",
                "list-objects",
                "--endpoint-url",
                endpoint,
                "--region",
                "auto",
                "--bucket",
                "tgpc",
                "--prefix",
                "backups/",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            return True

        data = json.loads(result.stdout)
        objects = sorted(data.get("Contents", []), key=lambda o: o["Key"], reverse=True)
        # Keep backups significantly larger than newest (avoid deleting good
        # large backups when a small one is created)
        new_backup_size = data.get("Contents", [{}])[0].get("Size", 0) if data.get("Contents") else 0
        for obj in objects[30:]:
            obj_size = obj.get("Size", 0)
            # Don't delete if this backup is >50% larger than the newest (likely a good full backup)
            if obj_size > new_backup_size * 1.5:
                logger.info(f"Keeping large R2 backup (size {obj_size} vs newest {new_backup_size}): {obj['Key']}")
                continue
            subprocess.run(
                [
                    "aws",
                    "s3api",
                    "delete-object",
                    "--endpoint-url",
                    endpoint,
                    "--region",
                    "auto",
                    "--bucket",
                    "tgpc",
                    "--key",
                    obj["Key"],
                ],
                capture_output=True,
                timeout=30,
                env=env,
            )
            logger.info(f"Removed old R2 backup: {obj['Key']}")
        return True

    def create(self, source: Path) -> str:
        """Create timestamped backup, upload to R2, and remove local copy."""
        if not source.exists():
            return ""

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = Path(self.config.data_directory) / "backups" / f"rph_backup_{ts}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        logger.info(f"Backup created: {dest}")

        uploaded = self._upload_to_r2(dest)
        if uploaded and dest.exists():
            dest.unlink()
            logger.info(f"Local backup deleted: {dest}")
        elif not uploaded:
            logger.warning(f"Local backup kept (R2 upload failed): {dest}")
        return str(dest)


class Manager:
    """Main management class."""

    def __init__(self):
        load_credentials()
        self.config = Config.load()
        self.file_manager = FileManager(self.config)
        self.backup_manager = BackupManager(self.config)
        self.scraper = Scraper()

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
                supabase = create_client(url, key)
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
            # Treat as missing, fall through to R2 restore
            try:
                rph_path.unlink(missing_ok=True)
            except Exception:
                pass

        logger.warning("data/rph.json not found — attempting restore from R2 backup...")
        endpoint = self.backup_manager._r2_endpoint()
        env = self.backup_manager._r2_env()
        if not endpoint:
            logger.error("Cannot restore: missing CLOUDFLARE_ACCOUNT_ID")
            return False

        # List backups to find latest
        r = subprocess.run(
            [
                "aws",
                "s3api",
                "list-objects-v2",
                "--endpoint-url",
                endpoint,
                "--region",
                "auto",
                "--bucket",
                "tgpc",
                "--prefix",
                "backups/",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if r.returncode != 0:
            logger.error(f"Cannot list R2 backups: {r.stderr.strip()}")
            return False

        data = json.loads(r.stdout)
        contents = data.get("Contents", [])
        if not contents:
            logger.error("No backups found in R2")
            return False

        latest = max(contents, key=lambda x: x["LastModified"])
        backup_key = latest["Key"]
        # Only restore if R2 backup is significantly larger than local (avoid overwriting test 1 with 1)
        if latest.get("Size", 0) < 100:
            logger.warning(f"R2 backup {backup_key} too small ({latest.get('Size')} bytes) — not restoring")
            return False

        rph_path.parent.mkdir(parents=True, exist_ok=True)
        r2 = subprocess.run(
            [
                "aws",
                "s3api",
                "get-object",
                "--endpoint-url",
                endpoint,
                "--region",
                "auto",
                "--bucket",
                "tgpc",
                "--key",
                backup_key,
                str(rph_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if r2.returncode != 0:
            rph_path.unlink(missing_ok=True)
            logger.error(f"Failed to restore from {backup_key}: {r2.stderr.strip()}")
            return False

        size = rph_path.stat().st_size
        logger.info(f"Restored data/rph.json from {backup_key} ({size} bytes)")
        return True

    def run_daily_update(self, force: bool = False):
        """Execute daily update workflow."""
        logger.info("Starting daily update...")

        # 0a. Restore rph.json from backup if missing (safety against accidental deletion)
        with Phase("Restore rph.json from backup", 1, 5):
            with heartbeat("Restoring rph.json from R2 backup"):
                self._restore_rph_from_backup()

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

        # Safety Check: Prevent massive data loss
        if existing_records and len(fresh_records) < len(existing_records) * 0.9:
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

    def sync_to_supabase(self, delta_records=None):
        """Sync data to Supabase. If delta_records provided, only upsert those.

        Returns True on success, False on failure so callers can propagate it.
        """
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SECRET_KEY")

        if not url or not key:
            logger.error("Missing Supabase credentials")
            return False

        try:
            supabase = create_client(url, key)

            if delta_records is not None:
                records = delta_records
            else:
                records = self.file_manager.load()

            if not records:
                logger.info("No records to sync")
                return True

            logger.info(f"Syncing {len(records)} records to Supabase...")

            # Batch upsert (5 core fields only — enrichment fields already in Supabase)
            batch_size = 1000
            num_batches = (len(records) + batch_size - 1) // batch_size
            with ProgressBar(total=num_batches, label="Upserting to Supabase") as bar:
                for i in range(0, len(records), batch_size):
                    batch = [r.to_dict() for r in records[i : i + batch_size]]
                    supabase.table("rph").upsert(batch, on_conflict="registration_number").execute()
                    bar.update(1, detail=f"batch {i // batch_size + 1}/{num_batches}")

            # Update last_sync timestamp in metadata table
            try:
                sync_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                supabase.table("metadata").upsert({"key": "last_sync", "value": sync_time}, on_conflict="key").execute()
                logger.info(f"Updated last_sync timestamp: {sync_time}")
            except Exception as e:
                logger.warning(f"Could not update metadata (table may not exist): {e}")

            logger.info("Supabase sync complete")

            # Post-sync verification: compare counts
            local_count = len(self.file_manager.load())
            try:
                result = supabase.table("rph").select("registration_number", count="exact").execute()
                remote_count = result.count
                if local_count != remote_count:
                    logger.warning(
                        f"Supabase mismatch detected (local={local_count}, remote={remote_count}). "
                        f"Running full sync to fix..."
                    )
                    all_records = self.file_manager.load()
                    fix_batches = (len(all_records) + batch_size - 1) // batch_size
                    with ProgressBar(total=fix_batches, label="Full re-sync to Supabase") as bar:
                        for i in range(0, len(all_records), batch_size):
                            batch = [r.to_dict() for r in all_records[i : i + batch_size]]
                            supabase.table("rph").upsert(batch, on_conflict="registration_number").execute()
                            bar.update(1, detail=f"batch {i // batch_size + 1}/{fix_batches}")
                    result = supabase.table("rph").select("registration_number", count="exact").execute()
                    remote_count = result.count
                    if local_count == remote_count:
                        logger.info(f"Full sync fixed mismatch. Supabase now has {remote_count} records")
                    else:
                        logger.critical(
                            f"SUPABASE STILL MISMATCHED after full sync: local={local_count}, remote={remote_count}"
                        )
                else:
                    logger.info(f"Supabase verification OK: {local_count} records match")
            except Exception as e:
                logger.warning(f"Could not verify Supabase count: {e}")

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return False

        return True

    def delete_removed_from_supabase(self, removed_ids: set) -> bool:
        """Delete removed records from Supabase."""
        if not removed_ids:
            return True
        url = __import__("os").environ.get("SUPABASE_URL")
        key = __import__("os").environ.get("SUPABASE_SECRET_KEY")
        if not url or not key:
            return False
        try:
            from supabase import create_client

            supabase = create_client(url, key)
            # Batch delete (Supabase IN limit)
            ids = list(removed_ids)
            batch = 500
            for i in range(0, len(ids), batch):
                supabase.table("rph").delete().in_("registration_number", ids[i : i + batch]).execute()
            __import__("logging").getLogger("tgpc").info(f"Deleted {len(ids)} removed records from Supabase")
            return True
        except Exception as e:
            __import__("logging").getLogger("tgpc").error(f"Delete failed: {e}")
            return False

    def sync_to_supabase_storage(self):
        """Upload rph.json to Supabase Storage (tgpc bucket)."""
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SECRET_KEY")
        if not url or not key:
            logger.error("Missing Supabase credentials")
            return False
        file_path = self.file_manager.data_dir / "rph.json"
        with heartbeat("Syncing rph.json to Supabase Storage"):
            try:
                with open(file_path, "rb") as f:
                    resp = requests.post(
                        f"{url}/storage/v1/object/tgpc/rph.json",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "apikey": key,
                            "x-upsert": "true",
                        },
                        data=f,
                        timeout=300,
                    )
                if resp.ok:
                    logger.info("Supabase Storage sync complete")
                    return True
                else:
                    logger.error(f"Supabase Storage sync failed: {resp.status_code} {resp.text}")
                    return False
            except Exception as e:
                logger.error(f"Supabase Storage sync error: {e}")
                return False

    def sync_to_r2(self):
        """Sync rph.json to Cloudflare R2. Photos are uploaded during enrichment.

        Returns True on success, False on failure so callers can propagate it.
        """
        endpoint = self._get_r2_endpoint()
        if not endpoint:
            logger.error("Missing CLOUDFLARE_ACCOUNT_ID for R2 sync")
            return False

        access_key = os.environ.get("R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        if not all([access_key, secret_key]):
            logger.error("Missing R2 credentials")
            return False

        file_path = str(self.file_manager.data_dir / "rph.json")

        # Upload rph.json
        with heartbeat("Uploading rph.json to R2"):
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
                        "rph.json",
                        "--body",
                        file_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=self._get_r2_env(),
                )
                if result.returncode == 0:
                    logger.info("R2 rph.json sync complete")
                    return True
                else:
                    logger.error(f"R2 rph.json sync failed: {result.stderr.strip()}")
                    return False
            except FileNotFoundError:
                logger.error("awscli not installed. Run: pip install awscli")
                return False
            except Exception as e:
                logger.error(f"R2 rph.json sync error: {e}")
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
                r2_key = f"photos/{reg_no}.webp"

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
                    r2_key,
                    "--body",
                    str(local_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                env=self._get_r2_env(),
            )
            if result.returncode == 0:
                return True
            logger.error(f"R2 upload failed for {r2_key}: {result.stderr.strip()}")
            return False
        except FileNotFoundError:
            logger.error("awscli not installed. Run: pip install awscli")
            return False
        except Exception as e:
            logger.error(f"R2 upload error for {r2_key}: {e}")
            return False

    def _verify_photo_on_r2(self, r2_key, expected_size):
        """Cloud-only verification: confirm file exists on R2 with correct size."""
        endpoint = self._get_r2_endpoint()
        if not endpoint:
            return False

        try:
            result = subprocess.run(
                [
                    "aws",
                    "s3api",
                    "head-object",
                    "--endpoint-url",
                    endpoint,
                    "--region",
                    "auto",
                    "--bucket",
                    "tgpc",
                    "--key",
                    r2_key,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=self._get_r2_env(),
            )
            if result.returncode == 0:
                head = json.loads(result.stdout)
                actual_size = head.get("ContentLength", -1)
                if actual_size == expected_size:
                    return True
                logger.warning(
                    f"R2 verification size mismatch for {r2_key}: expected {expected_size}, got {actual_size}"
                )
                return False
            logger.error(f"R2 head-object failed for {r2_key}: {result.stderr.strip()}")
            return False
        except Exception as e:
            logger.error(f"R2 verification error for {r2_key}: {e}")
            return False

    def sync_to_gdrive(self):
        """Sync rph.json to Google Drive via rclone.

        Returns True on success, False on failure so callers can propagate it.
        """
        gdrive_config_b64 = os.environ.get("RCLONE_GDRIVE_CONFIG")
        if not gdrive_config_b64:
            logger.error("Missing RCLONE_GDRIVE_CONFIG")
            return False

        config_path = Path("/tmp/rclone-gdrive.conf")
        with heartbeat("Syncing rph.json to Google Drive"):
            try:
                import base64

                config_path.write_bytes(base64.b64decode(gdrive_config_b64))
                result = subprocess.run(
                    [
                        "rclone",
                        "copyto",
                        str(self.file_manager.data_dir / "rph.json"),
                        "gdrive:tgpc/rph.json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env={**os.environ, "RCLONE_CONFIG": str(config_path)},
                )
                config_path.unlink(missing_ok=True)
                if result.returncode == 0:
                    logger.info("GDrive sync complete")
                    return True
                else:
                    logger.error(f"GDrive sync failed: {result.stderr.strip()}")
                    return False
            except FileNotFoundError:
                logger.error("rclone not installed")
                return False
            except Exception as e:
                logger.error(f"GDrive sync error: {e}")
                return False

    def sync_to_release(self):
        """Encrypt rph.json and upload to GitHub Release.

        Returns True on success, False on failure so callers can propagate it.
        """
        tag = "rphjson"
        file_path = str(self.file_manager.data_dir / "rph.json")
        archive_path = file_path + ".zip"
        repo = os.environ.get("GITHUB_REPOSITORY", "tgpc-org/tgpc")
        password = os.environ.get("RELEASE_PASSWORD")

        if not password:
            logger.warning("RELEASE_PASSWORD not set, skipping release sync")
            return True

        try:
            with open(file_path) as f:
                count = len(json.load(f))
        except Exception:
            count = 0

        title = f"{count:,} records — rph.json"

        with heartbeat("Publishing encrypted GitHub Release"):
            try:
                # AES-encrypted zip built in-process (CODE_REVIEW.md H3) so the
                # password never appears in a process argument list, unlike the
                # previous `zip -P` invocation.
                import pyzipper

                with pyzipper.AESZipFile(
                    archive_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
                ) as zf:
                    zf.setpassword(password.encode("utf-8"))
                    zf.write(file_path, arcname="rph.json")
                logger.info(f"Encrypted release archive created ({count:,} records)")

                result = subprocess.run(
                    ["gh", "release", "view", tag, "--repo", repo],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode != 0:
                    logger.info(f"Creating release {tag}...")
                    subprocess.run(
                        ["gh", "release", "create", tag, "--repo", repo, "--title", title, "--notes", title],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                subprocess.run(
                    ["gh", "release", "upload", tag, archive_path, "--repo", repo, "--clobber"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                subprocess.run(
                    ["gh", "release", "edit", tag, "--repo", repo, "--title", title, "--notes", title],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                logger.info(f"Release sync complete ({count:,} records)")
                return True
            except ImportError:
                logger.error("pyzipper not installed. Run: pip install pyzipper")
                return False
            except FileNotFoundError:
                logger.error("gh CLI not installed")
                return False
            except Exception as e:
                logger.error(f"Release sync error: {e}")
                return False
            finally:
                if os.path.exists(archive_path):
                    os.remove(archive_path)

    def sync_to_email(self):
        """Send update report via Resend email.

        Returns True on success, False on failure so callers can propagate it.
        """
        api_key = os.environ.get("RESEND_API_KEY")
        recipient = os.environ.get("NOTIFICATION_EMAIL")
        if not api_key or not recipient:
            logger.warning("Missing RESEND_API_KEY or NOTIFICATION_EMAIL")
            return True

        data = getattr(self, "_last_update_details", None)
        if not data:
            logger.info("No update details found — skipping email")
            return True

        new = data.get("new_details", [])
        mod = data.get("modified_details", [])
        rem = data.get("removed_details", [])

        if not new and not mod and not rem:
            logger.info("No changes — skipping email")
            return True

        import re

        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist)
        sync_time = now.strftime("%d %B %Y, %A, %H:%M IST")
        subj_time = now.strftime("%d%b%Y %a %H:%M IST").upper()

        def cat(s):
            m = re.search(r"\((.*?)\)", s)
            if not m:
                return "Other"
            return {
                "BPharm": "BPharm",
                "MPharm": "MPharm",
                "DPharm": "DPharm",
                "PharmD": "PharmD",
                "QC": "QC",
                "QP": "QP",
            }.get(m.group(1), m.group(1).title())

        def reg_no(s):
            m = re.search(r"(\d+)", s)
            return int(m.group(1)) if m else 0

        def fmt_html(title, items, color):
            if not items:
                return ""
            grouped = {}
            for i in items:
                grouped.setdefault(cat(i), []).append(i)
            html = f'<div style="margin-bottom:35px;"><h4 style="margin:0 0 16px;color:{color};font-size:14px;font-weight:700;text-transform:uppercase;border-bottom:2px solid {color};padding-bottom:6px;display:inline-block;letter-spacing:.5px;">{title} ({len(items)})</h4>'  # noqa: E501
            for c in sorted(grouped):
                recs = grouped[c]
                html += f'<div style="margin-bottom:18px;"><div style="font-size:11px;font-weight:700;color:#111;text-transform:uppercase;margin-bottom:6px;letter-spacing:1px;">{c} ({len(recs)})</div>'  # noqa: E501
                for r in sorted(recs, key=reg_no):
                    parts = r.split(" - ", 1)
                    reg = parts[0]
                    name = re.sub(r"\s*\(.*?\)$", "", parts[1] if len(parts) > 1 else r).strip()
                    html += f'<div style="font-size:13px;color:#6b7280;padding:4px 0;"><span style="font-family:ui-monospace,monospace;">{reg}</span> - {name}</div>'  # noqa: E501
                html += "</div>"
            return html + "</div>"

        new_t, mod_t, rem_t = new, mod, rem

        text = f"TGPC RPh Index Sync Report\n{sync_time}\n\n"
        html = (
            '<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
            '<body style="font-family:-apple-system,sans-serif;background:#fff;padding:15px 20px;color:#333;line-height:1.3;margin:0;">'  # noqa: E501
            '<div style="max-width:600px;">'
            f'<h2 style="margin:0;font-size:17px;line-height:1.2;"><span style="color:#00cc66;">TGPC</span> <span style="color:#ef4444;">RPh</span> <span style="color:#808080;">Index</span> Sync Report</h2>'  # noqa: E501
            f'<div style="color:#666;font-size:12px;margin-bottom:30px;font-weight:500;">{sync_time}</div>'
            f"{fmt_html('🌱 NEW', new_t, '#00cc66')}{fmt_html('🌀 CHANGES', mod_t, '#3b82f6')}{fmt_html('❌ REMOVALS', rem_t, '#ef4444')}"  # noqa: E501
            '<div style="margin-top:15px;font-size:11px;color:#888;padding-top:10px;">'
            '<div style="font-weight:700;"><span style="color:#00cc66;">TGPC</span> <span style="color:#ef4444;">RPh</span> <span style="color:#808080;">Index</span></div>'  # noqa: E501
            "<div>Open-Source TGPC Pharmacist Data</div></div></div></body></html>"
        )
        for label, items, total in [("NEW", new_t, new), ("CHANGES", mod_t, mod), ("REMOVALS", rem_t, rem)]:
            if total:
                text += (
                    f"{label} ({len(total)}):\n" + "\n".join(sorted(items, key=lambda x: (cat(x), reg_no(x)))) + "\n\n"
                )
        text += "---\nTGPC RPh Index\nOpen-Source TGPC Pharmacist Data"

        total = data.get("total_records", 0)
        total_fmt = f"{total:,}"

        change_parts = []
        if new:
            change_parts.append(f"+{len(new)}")
        if mod:
            change_parts.append(f"~{len(mod)}")
        if rem:
            change_parts.append(f"-{len(rem)}")

        change_str = f"({' '.join(change_parts)})" if change_parts else ""
        subject = f"{total_fmt} {change_str}-RPh Data Sync-{subj_time}"

        with heartbeat("Sending email report via Resend"):
            try:
                # requests instead of a curl argv so the API key never appears
                # in the process argument list (CODE_REVIEW.md H3).
                resp = requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "from": "RPh Data Sync <onboarding@resend.dev>",
                        "to": [recipient],
                        "subject": subject,
                        "text": text,
                        "html": html,
                    },
                    timeout=30,
                )
                if resp.ok:
                    logger.info(f"Email sent: {resp.text.strip()}")
                    return True
                logger.warning(f"Resend API error: {resp.status_code} {resp.text.strip()}")
                return False
            except Exception as e:
                logger.warning(f"Email send error: {e}")
                return False

    def run_enrichment(
        self,
        start: int = 1,
        stop: int = None,
    ):
        """
        Run enrichment for serial number range.

        Args:
            start: Start from serial number (default: 1)
            stop: Stop at serial number (default: all)
        """

        # Health check
        if not self.scraper.health_check():
            logger.error("Health check failed. Aborting enrichment.")
            return

        logger.info("Starting enrichment...")

        # Create Supabase client for live upsert
        supabase = None
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SECRET_KEY")
        if url and key:
            supabase = create_client(url, key)

        # Load Data
        rph_records = self.file_manager.load("rph.json")

        # Create lookup by registration number (serial_number is nullable/non-unique)
        rph_lookup = {r.registration_number: r for r in rph_records}

        # Identify Pending - check Supabase for already enriched records
        done_ids = set()
        if supabase:
            BATCH = 1000
            for i in range(0, len(rph_records), BATCH):
                end = min(i + BATCH - 1, len(rph_records) - 1)
                try:
                    resp = (
                        supabase.table("rph")
                        .select("registration_number, gender, validity_date, status, education, work_experience")
                        .order("registration_number")
                        .range(i, end)
                        .execute()
                    )
                    for r in resp.data:
                        if (
                            r.get("gender")
                            or r.get("validity_date")
                            or r.get("status")
                            or r.get("education")
                            or r.get("work_experience")
                        ):
                            done_ids.add(r["registration_number"])
                except Exception as e:
                    logger.warning(f"Failed to check enrichment status batch {i}: {e}")
            logger.info(f"Found {len(done_ids)} already enriched records in Supabase")
        else:
            logger.warning("Supabase credentials missing — all records will be considered pending")

        # Sort by serial number ascending (start from serial 1)
        pending_records = [r for r in rph_records if r.registration_number not in done_ids]
        pending_records.sort(key=lambda r: r.serial_number or 0)

        if not pending_records:
            logger.info("No pending records to enrich.")
            return

        total_pending = len(pending_records)
        logger.info(f"Total pending: {total_pending} records")

        # Setup Photos Directory
        img_dir = Path(self.config.enrichment_directory) / "webp"
        img_dir.mkdir(parents=True, exist_ok=True)

        # Filter by start/stop range - use serial_number from rph.json as position
        if start != 1 or stop is not None:
            rph_records_all = self.file_manager.load("rph.json")
            rph_records_all.sort(key=lambda r: r.serial_number or 0)

            filtered = []
            for i, r in enumerate(rph_records_all):
                if start and i + 1 < start:
                    continue
                if stop and i + 1 > stop:
                    break
                if r.registration_number not in done_ids:
                    filtered.append(r)
            pending_records = filtered

            start_str = f"serial {start}" if start else "all"
            stop_str = f"serial {stop}" if stop else "end"
            logger.info(f"Processing {start_str} to {stop_str} ({len(pending_records)} records)")

        if not pending_records:
            logger.info("No records in range.")
            return

        # Process records sequentially
        total_processed = self._process_records_sequential(pending_records, rph_lookup, img_dir, supabase=supabase)

        # Keep progress file for tracking
        if total_processed > 0:
            logger.info(f"Enrichment complete: {total_processed} records processed")
        else:
            logger.info("No records processed")

    def enrich_new_records(self, force: bool = False):
        """Auto-enrich records that need enrichment.

        Args:
            force: If False, abort when >1000 candidate records (safety guard against
                   corrupt/missing rph.json causing full re-enrichment).
                   If True, enrich all records missing enrichment data.
        """
        regs = getattr(self, "_last_new_regs", set())

        # In force mode, enrich all records missing enrichment data
        if force:
            records = self.file_manager.load()
            logger.info(f"Force mode: checking {len(records)} records for enrichment needs")
        else:
            regs = getattr(self, "_last_new_regs", set())
            # Safety cap: require --force if >1000 new records
            if len(regs) > 1000 and not force:
                logger.error(
                    f"SAFETY ABORT: {len(regs)} new records detected (limit 1000). "
                    "This usually means data/rph.json is missing or corrupted, "
                    "causing all records to appear as 'new'. "
                    "Run with --force to override and enrich all."
                )
                return

            if not regs:
                logger.info("No new records to enrich")
                return

            records = [r for r in self.file_manager.load() if r.registration_number in regs]
        if not records:
            logger.info("No matching records found in rph.json")
            return

        # Check Supabase for already-enriched records (filtered by registration number)
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SECRET_KEY")
        supabase = create_client(url, key) if url and key else None

        done_ids: set = set()
        if supabase:
            reg_list = [r.registration_number for r in records]
            BATCH = 500
            for i in range(0, len(reg_list), BATCH):
                batch = reg_list[i : i + BATCH]
                try:
                    resp = (
                        supabase.table("rph")
                        .select("registration_number, gender, validity_date, status, education, work_experience")
                        .in_("registration_number", batch)
                        .execute()
                    )
                    for r in resp.data:
                        if (
                            r.get("gender")
                            or r.get("validity_date")
                            or r.get("status")
                            or r.get("education")
                            or r.get("work_experience")
                        ):
                            done_ids.add(r["registration_number"])
                except Exception as e:
                    logger.warning(f"Failed to check enrichment status batch {i}: {e}")

            if done_ids:
                logger.info(f"Skipping {len(done_ids)} already-enriched records")
                records = [r for r in records if r.registration_number not in done_ids]
        else:
            logger.warning("Supabase credentials missing — cannot check enrichment status")

        if not records:
            logger.info("All records already enriched — nothing to do")
            return

        # Setup shared resources
        img_dir = Path(self.config.enrichment_directory) / "webp"
        img_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Enriching {len(records)} records...")
        rph_lookup = {r.registration_number: r for r in records}

        processed = self._process_records_sequential(records, rph_lookup, img_dir, supabase=supabase)
        logger.info(f"Enrich complete: {processed} records processed")

    def _process_records_sequential(
        self, pending_records, rph_lookup, img_dir, ip_rotation_interval=500, supabase=None
    ):
        """Process records sequentially."""
        total_processed = 0
        failed_photos = []

        with ProgressBar(total=len(pending_records), label="Enriching records") as bar:
            for idx, record in enumerate(pending_records):
                serial = record.serial_number
                reg_no = record.registration_number
                bar.set_detail(reg_no)
                try:
                    # Scrape using original synchronous scraper
                    step(f"fetching details for {reg_no}")
                    details = self.scraper.extract_detailed_info(reg_no, img_dir)
                    if not details:
                        continue

                    # Upload photo to R2, verify, delete local
                    photo_file = img_dir / f"{reg_no}.webp"
                    if photo_file.is_file():
                        r2_key = f"photos/{reg_no}.webp"

                        # photo_url is only recorded when the upload actually
                        # succeeded — a failed upload must not leave Supabase
                        # pointing at an object that does not exist.
                        if self.upload_and_verify_photo(photo_file, r2_key):
                            photo_file.unlink()
                            logger.info(f"Photo uploaded + verified + local deleted: {reg_no}")
                            if os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
                                details.photo_url = f"{self.config.r2_public_base}/{r2_key}"
                        else:
                            failed_photos.append(reg_no)
                            logger.error(f"FAILED after 5 attempts: {reg_no} — local file kept at {photo_file}")

                    # Get basic info from rph.json lookup for validation
                    basic_info = rph_lookup.get(reg_no)

                    # CRITICAL SAFETY CHECK - Validate all details match
                    step(f"validating {reg_no}")
                    mismatches = []
                    if details.registration_number and details.registration_number.lower() != reg_no.lower():
                        mismatches.append(
                            f"registration_number: expected '{reg_no}', got '{details.registration_number}'"
                        )
                    if details.name and basic_info and details.name.strip().lower() != basic_info.name.strip().lower():
                        mismatches.append(f"name: expected '{basic_info.name}', got '{details.name}'")
                    if (
                        details.father_name
                        and basic_info
                        and details.father_name.strip().lower() != basic_info.father_name.strip().lower()
                    ):
                        mismatches.append(
                            f"father_name: expected '{basic_info.father_name}', got '{details.father_name}'"
                        )
                    if (
                        details.category
                        and basic_info
                        and details.category.strip().lower() != basic_info.category.strip().lower()
                    ):
                        mismatches.append(f"category: expected '{basic_info.category}', got '{details.category}'")

                    if mismatches:
                        logger.critical(
                            f"DATA CORRUPTION PREVENTED for serial {serial} ({reg_no}): " + "; ".join(mismatches)
                        )
                        raise DataIntegrityError(
                            f"Data Integrity Violation: Mismatched fields for {reg_no}. Stopping to prevent corruption."
                        )

                    logger.info(
                        f"✅ DATA VALIDATION PASSED: serial {serial} ({reg_no}) - {details.name} ({details.category})"
                    )

                    basic_info = rph_lookup.get(reg_no)
                    if not basic_info:
                        logger.warning(f"Basic info not found for {reg_no}, using scraped data")

                    basic_data = {
                        "registration_number": (
                            basic_info.registration_number if basic_info else details.registration_number
                        ),
                        "name": (basic_info.name if basic_info else details.name),
                        "father_name": (basic_info.father_name if basic_info else details.father_name),
                        "gender": details.gender or "",
                        "category": (basic_info.category if basic_info else details.category),
                        "status": details.status or "",
                        "serial_number": (basic_info.serial_number if basic_info else None),
                    }

                    # Combine basic info + extracted details
                    extracted_data = details.to_detailed_dict()
                    data = {**extracted_data, **basic_data}

                    total_processed += 1

                    # Upsert to Supabase immediately
                    if supabase:
                        step(f"upserting {reg_no} to Supabase")
                        try:
                            supabase.table("rph").upsert(data, on_conflict="registration_number").execute()
                        except Exception as e:
                            logger.warning(f"Failed to upsert enriched record {reg_no} to Supabase: {e}")

                except DataIntegrityError:
                    raise
                except Exception as e:
                    logger.error(f"Enrichment failed for {reg_no}: {e}")
                finally:
                    bar.update(1, detail=reg_no)

        if failed_photos:
            logger.critical(
                f"PHOTO UPLOAD FAILED for {len(failed_photos)} records after 5 attempts each: "
                + ", ".join(failed_photos)
            )
            logger.critical("Local files kept at data/webp/ — manually retry or investigate R2 connectivity")

        return total_processed
