"""
Record enrichment for TGPC: fetch detail pages, validate against the base
registry, upload photos to R2, and upsert the full row to Supabase.

Split out of manager.py so the orchestration module stays focused. The
functions take the Manager as their first argument and construct clients
through manager._make_supabase_client(), so tests can keep patching
tgpc.manager.create_client and tgpc.manager.Scraper.
"""

import os
import re
from pathlib import Path

from tgpc.progress import ProgressBar, step
from tgpc.storage import DataIntegrityError
from tgpc.utils import setup_logging


logger = setup_logging("tgpc.enrichment")


def run_enrichment(manager, start: int = 1, stop: int = None):
    """
    Run enrichment for serial number range.

    Args:
        start: Start from serial number (default: 1)
        stop: Stop at serial number (default: all)
    """

    # Health check
    if not manager.scraper.health_check():
        logger.error("Health check failed. Aborting enrichment.")
        return

    logger.info("Starting enrichment...")

    # Create Supabase client for live upsert
    supabase = None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if url and key:
        supabase = manager._make_supabase_client(url, key)

    # Load Data
    rph_records = manager.file_manager.load("rph.json")

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
    img_dir = Path(manager.config.enrichment_directory) / "webp"
    img_dir.mkdir(parents=True, exist_ok=True)

    # Filter by start/stop range on actual serial_number (not list position:
    # serials have gaps and None values, so position != serial).
    if start != 1 or stop is not None:
        rph_records_all = manager.file_manager.load("rph.json")
        rph_records_all.sort(key=lambda r: r.serial_number or 0)

        filtered = []
        none_serial = 0
        for r in rph_records_all:
            s = r.serial_number
            if s is None:
                none_serial += 1
                continue
            if start and s < start:
                continue
            if stop and s > stop:
                continue
            if r.registration_number not in done_ids:
                filtered.append(r)
        if none_serial:
            logger.warning(
                f"Skipped {none_serial} records with no serial_number (cannot be addressed by a serial range)"
            )
        pending_records = filtered

        start_str = f"serial {start}" if start else "all"
        stop_str = f"serial {stop}" if stop else "end"
        logger.info(f"Processing {start_str} to {stop_str} ({len(pending_records)} records)")

    if not pending_records:
        logger.info("No records in range.")
        return

    # Process records sequentially
    total_processed = process_records_sequential(manager, pending_records, rph_lookup, img_dir, supabase=supabase)

    # Keep progress file for tracking
    if total_processed > 0:
        logger.info(f"Enrichment complete: {total_processed} records processed")
    else:
        logger.info("No records processed")


def enrich_new_records(manager, force: bool = False):
    """Auto-enrich records that need enrichment.

    Args:
        force: If False, abort when >1000 candidate records (safety guard against
               corrupt/missing rph.json causing full re-enrichment).
               If True, enrich all records missing enrichment data.
    """
    # Standalone `python3 -m tgpc enrich` runs in a fresh process where
    # _last_new_regs was never set (vs. an update in the same process
    # that found zero new records). Without this fallback the command
    # always no-ops with "No new records to enrich".
    if not hasattr(manager, "_last_new_regs"):
        records = manager.file_manager.load()
        # Same 1000-record safety cap as the update path: a standalone
        # run with no Supabase credentials would otherwise treat all
        # ~89k records as pending and scrape every one.
        if len(records) > 1000 and not force:
            logger.error(
                f"SAFETY ABORT: {len(records)} candidate records (limit 1000). "
                "Run with --force to enrich all records missing enrichment data."
            )
            return
        logger.info(f"Standalone enrich: checking {len(records)} records for enrichment needs")
    elif force:
        records = manager.file_manager.load()
        logger.info(f"Force mode: checking {len(records)} records for enrichment needs")
    else:
        regs = getattr(manager, "_last_new_regs", set())
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

        records = [r for r in manager.file_manager.load() if r.registration_number in regs]
    if not records:
        logger.info("No matching records found in rph.json")
        return

    # Check Supabase for already-enriched records (filtered by registration number)
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    supabase = manager._make_supabase_client(url, key) if url and key else None

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
    img_dir = Path(manager.config.enrichment_directory) / "webp"
    img_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Enriching {len(records)} records...")
    rph_lookup = {r.registration_number: r for r in records}

    processed = process_records_sequential(manager, records, rph_lookup, img_dir, supabase=supabase)
    logger.info(f"Enrich complete: {processed} records processed")


def process_records_sequential(manager, pending_records, rph_lookup, img_dir, ip_rotation_interval=500, supabase=None):
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
                details = manager.scraper.extract_detailed_info(reg_no, img_dir)
                if not details:
                    logger.warning(f"No details found at source for {reg_no} — skipping enrichment")
                    continue

                # Upload photo to R2, verify, delete local
                photo_file = img_dir / f"{reg_no}.webp"
                if photo_file.is_file():
                    safe_reg = re.sub(r"[^A-Za-z0-9._-]", "", reg_no)
                    r2_key = f"photos/{safe_reg}.webp"

                    # photo_url is only recorded when the upload actually
                    # succeeded — a failed upload must not leave Supabase
                    # pointing at an object that does not exist.
                    if manager.upload_and_verify_photo(photo_file, r2_key):
                        photo_file.unlink()
                        logger.info(f"Photo uploaded + verified + local deleted: {reg_no}")
                        if os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
                            details.photo_url = f"{manager.config.r2_public_base}/{r2_key}"
                    else:
                        failed_photos.append(reg_no)
                        logger.error(f"FAILED after 5 attempts: {reg_no} — local file kept at {photo_file}")

                # Get basic info from rph.json lookup for validation
                basic_info = rph_lookup.get(reg_no)

                # CRITICAL SAFETY CHECK - Validate all details match
                step(f"validating {reg_no}")
                mismatches = []
                if details.registration_number and details.registration_number.lower() != reg_no.lower():
                    mismatches.append(f"registration_number: expected '{reg_no}', got '{details.registration_number}'")
                if details.name and basic_info and details.name.strip().lower() != basic_info.name.strip().lower():
                    mismatches.append(f"name: expected '{basic_info.name}', got '{details.name}'")
                if (
                    details.father_name
                    and basic_info
                    and details.father_name.strip().lower() != basic_info.father_name.strip().lower()
                ):
                    mismatches.append(f"father_name: expected '{basic_info.father_name}', got '{details.father_name}'")
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
            f"PHOTO UPLOAD FAILED for {len(failed_photos)} records after 5 attempts each: " + ", ".join(failed_photos)
        )
        logger.critical("Local files kept at data/webp/ — manually retry or investigate R2 connectivity")

    return total_processed
