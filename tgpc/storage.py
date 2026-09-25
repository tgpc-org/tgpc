"""
Data & storage primitives for TGPC: local JSON storage, R2 backups, and
the boto3-based Cloudflare R2 client.

Split out of manager.py so the orchestration module stays focused; the
names are re-exported from tgpc.manager for backward compatibility
(tests patch tgpc.manager.R2Client / tgpc.manager.BackupManager.*).
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

import boto3
from botocore.config import Config as BotocoreConfig

from tgpc.scraper import PharmacistRecord
from tgpc.utils import Config, setup_logging


logger = setup_logging("tgpc.storage")


class R2Client:
    """Small boto3 wrapper for Cloudflare R2 S3 operations."""

    BUCKET = "tgpc"
    REGION = "auto"

    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        self.endpoint = endpoint
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=self.REGION,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotocoreConfig(signature_version="s3v4"),
        )

    def put_object(self, key: str, path: Path) -> bool:
        with open(path, "rb") as data:
            self.client.put_object(Bucket=self.BUCKET, Key=key, Body=data)
        return True

    def list_objects(self, prefix: str) -> list:
        return self.client.list_objects_v2(Bucket=self.BUCKET, Prefix=prefix).get("Contents", [])

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.BUCKET, Key=key)

    def get_object(self, key: str, destination: Path) -> bool:
        self.client.download_file(self.BUCKET, key, str(destination))
        return True

    def head_object(self, key: str) -> dict:
        return self.client.head_object(Bucket=self.BUCKET, Key=key)


class DataIntegrityError(RuntimeError):
    """Raised when scraped detail data does not match the requested record."""


def validate_rph_backup(path: Path, expected_min_records: int) -> int:
    """Validate a downloaded rph.json backup and return its record count.

    A record count alone cannot be trusted: a truncated, padded or
    partially-written upload can clear the minimum while carrying unusable rows,
    and this payload replaces the whole registry. Raises ValueError when the
    backup cannot safely be restored (audit lead 8: restore validated by count
    only).
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"backup is a {type(data).__name__}, expected a JSON list")
    if len(data) < expected_min_records:
        raise ValueError(f"backup has {len(data)} records, expected >= {expected_min_records}")
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"record {index} is a {type(row).__name__}, expected an object")
        registration = row.get("registration_number")
        if not isinstance(registration, str) or not registration.strip():
            raise ValueError(f"record {index} has no usable registration_number")
    return len(data)


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
        # Validate tmp before rename: the round-trip must preserve every record.
        with open(tmp_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if not isinstance(loaded, list) or len(loaded) != len(records):
                raise ValueError(
                    f"Tmp validation failed: wrote {len(records)} records, read back "
                    f"{len(loaded) if isinstance(loaded, list) else 'non-list'}"
                )
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


def _make_r2_client(endpoint, access_key, secret_key):
    """Construct R2Client via tgpc.manager's namespace.

    Historical patch target for backup-upload tests is tgpc.manager.R2Client;
    resolving lazily at call time (both modules are fully loaded by then)
    keeps those patches effective for BackupManager.
    """
    from tgpc import manager

    return manager.R2Client(endpoint, access_key, secret_key)


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
        if not endpoint:
            logger.warning("Missing CLOUDFLARE_ACCOUNT_ID — skipping R2 backup upload")
            return False

        access_key = os.environ.get("R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        if not all([access_key, secret_key]):
            logger.warning("Missing R2 credentials — skipping R2 backup upload")
            return False

        client = _make_r2_client(endpoint, access_key, secret_key)
        r2_key = f"backups/{local_path.name}"

        try:
            client.put_object(r2_key, local_path)
            logger.info(f"Backup uploaded to R2: {r2_key}")
        except Exception as e:
            logger.warning(f"R2 backup upload failed for {r2_key}: {e}")
            return False

        # List backups and delete old ones (keep newest 30, skip very large ones)
        try:
            objects = client.list_objects("backups/")
            objects = sorted(objects, key=lambda o: o["Size"], reverse=True)  # largest first
            if len(objects) > 30:
                new_backup_size = objects[0]["Size"] if objects else 0
                for obj in objects[30:]:
                    obj_size = obj["Size"]
                    # Don't delete if this backup is >50% larger than the newest (likely a good full backup)
                    if obj_size > new_backup_size * 1.5:
                        logger.info(
                            f"Keeping large R2 backup (size {obj_size} vs newest {new_backup_size}): {obj['Key']}"
                        )
                        continue
                    client.delete_object(obj["Key"])
                    logger.info(f"Removed old R2 backup: {obj['Key']}")
        except Exception as e:
            logger.warning(f"Failed to list/delete old R2 backups: {e}")
            # Don't fail the upload for cleanup errors

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
