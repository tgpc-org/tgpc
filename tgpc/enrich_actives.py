"""
Parallel enrichment + upsert for now-active records found by the inactive sweep.

Reads data/now_active_from_inactive.jsonl, enriches each record (photo -> WebP
-> R2 upload+verify -> local delete), validates identity against the record's
basic info, and upserts to Supabase. Runs N workers in parallel (each with its
own Scraper/session/rate limiter).

Usage:
    python3 -m tgpc.enrich_actives [--workers 4] [--min-delay 0.5]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVE_FILE = ROOT / "data" / "now_active_from_inactive.jsonl"

IDENTITY_FIELDS = ["registration_number", "name", "father_name", "category", "serial_number"]


def _sb_client():
    from tgpc.utils import load_credentials

    load_credentials()
    from supabase import create_client

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def main():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tgpc.progress import ProgressBar, step
    from tgpc.scraper import PharmacistRecord, Scraper
    from tgpc.manager import Manager

    parser = argparse.ArgumentParser(description="Parallel enrich + upsert actives from the inactive sweep")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default 4)")
    parser.add_argument("--min-delay", type=float, default=0.5, help="Rate-limiter floor seconds (default 0.5)")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N records")
    args = parser.parse_args()

    records = []
    for line in ACTIVE_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        records.append(
            PharmacistRecord(
                registration_number=d["registration_number"],
                name=d.get("name") or "",
                father_name=d.get("father_name") or "",
                category=d.get("category") or "",
                serial_number=d.get("serial_number"),
            )
        )
    if args.limit:
        records = records[: args.limit]
    total = len(records)
    print(f"Loaded {total} actives to enrich ({args.workers} workers, min_delay {args.min_delay}s)")

    mgr = Manager()
    supabase = _sb_client()
    img_dir = Path(mgr.config.enrichment_directory) / "webp"
    img_dir.mkdir(parents=True, exist_ok=True)
    # Keyed by registration_number (the primary key). serial_number is
    # nullable/non-unique — keying on it collides all None serials onto one
    # entry and can attribute one pharmacist's identity to another's record.
    rph_lookup = {r.registration_number: r for r in records}

    lock = threading.Lock()
    stats = {"processed": 0, "failed": 0, "no_photo": 0}
    failed_photos = []
    start = time.time()

    def make_scraper():
        sc = Scraper()
        sc.rate_limiter.min_delay = args.min_delay
        sc.rate_limiter.current_delay = args.min_delay
        return sc

    def enrich(sc, reg_no):
        try:
            step(f"fetching details for {reg_no}")
            details = sc.extract_detailed_info(reg_no, img_dir)
            if not details:
                with lock:
                    stats["failed"] += 1
                step(f"{reg_no}: NOT FOUND on source")
                return

            photo_file = img_dir / f"{reg_no}.webp"
            if photo_file.is_file():
                r2_key = f"photos/{reg_no}.webp"
                # Same contract as manager._process_records_sequential: record
                # photo_url only after a verified upload, never on failure.
                if mgr.upload_and_verify_photo(photo_file, r2_key):
                    photo_file.unlink()
                    step(f"{reg_no}: photo uploaded + verified + local deleted")
                    if os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
                        details.photo_url = f"{mgr.config.r2_public_base}/{r2_key}"
                else:
                    with lock:
                        failed_photos.append(reg_no)
                    step(f"{reg_no}: PHOTO FAILED (kept locally)")
            else:
                with lock:
                    stats["no_photo"] += 1
                step(f"{reg_no}: no photo")

            basic_info = rph_lookup.get(reg_no)

            # CRITICAL SAFETY CHECK - validate identity
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
                step(f"{reg_no}: DATA MISMATCH " + "; ".join(mismatches))
                raise ValueError(f"Data Integrity Violation: {'; '.join(mismatches)} for {reg_no}")

            basic_data = {
                "registration_number": basic_info.registration_number if basic_info else details.registration_number,
                "name": basic_info.name if basic_info else details.name,
                "father_name": basic_info.father_name if basic_info else details.father_name,
                "gender": details.gender or "",
                "category": basic_info.category if basic_info else details.category,
                "status": details.status or "",
                "serial_number": basic_info.serial_number if basic_info else None,
            }
            data = {**details.to_detailed_dict(), **basic_data}

            step(f"upserting {reg_no} to Supabase")
            supabase.table("rph").upsert(data, on_conflict="registration_number").execute()
            with lock:
                stats["processed"] += 1
            step(f"{reg_no}: ✅ upserted (Active)")
        except Exception as e:
            with lock:
                stats["failed"] += 1
            step(f"{reg_no}: ERROR {e}")
            print(f"ERROR {reg_no}: {e}", file=sys.stderr)

    with ProgressBar(total=total, label="Enriching actives", cadence=1) as bar:
        if args.workers <= 1:
            sc = make_scraper()
            for r in records:
                bar.set_detail(r.registration_number)
                enrich(sc, r.registration_number)
                bar.update(1, detail=r.registration_number)
        else:

            def worker(chunk):
                # One Scraper per worker: session reuse, connection pooling and
                # the rate limiter's learned state persist across the chunk
                # (CODE_REVIEW.md L4).
                sc = make_scraper()
                for r in chunk:
                    bar.set_detail(r.registration_number)
                    enrich(sc, r.registration_number)
                    bar.update(1, detail=r.registration_number)

            chunks = [records[i :: args.workers] for i in range(args.workers)]
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                list(pool.map(worker, chunks))

    elapsed = time.time() - start
    print(f"\nEnrich complete in {elapsed:.0f}s ({total / elapsed:.1f}/s)")
    print(f"Stats: {stats}")
    if failed_photos:
        print(f"PHOTO FAILED ({len(failed_photos)}): {', '.join(failed_photos[:20])}")


if __name__ == "__main__":
    main()
