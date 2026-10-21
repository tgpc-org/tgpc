"""Fill empty rph columns from the live source detail page.

Strictly additive: a column is written only when the DB value is blank AND
the source returns a non-blank value. Identity fields, status and photo_url
are never touched, so a re-scrape can never blank or relabel a record.

Used for the completeness backfill (e.g. rows imported without a gender).

Usage:
    python3 -m tgpc.backfill_missing --ids-file data/backfill_gender.jsonl \
        --workers 8 --min-delay 0.5
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from supabase import create_client  # noqa: F401  (patch target: tgpc.backfill_missing.create_client)

from tgpc.progress import ProgressBar, step
from tgpc.scraper import Scraper
from tgpc.utils import load_credentials, setup_logging

logger = setup_logging("tgpc.backfill")

# Source field -> rph column. Only blank columns are ever written.
FILLABLE = ("gender", "validity_date")
FETCH_COLS = "registration_number,name,gender,validity_date"
PAGE = 1000


def _blank(value) -> bool:
    return not (value or "").strip()


def _supabase():
    import os

    load_credentials()
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def _fetch_current(sb, regs):
    current = {}
    for i in range(0, len(regs), PAGE):
        chunk = regs[i : i + PAGE]
        rows = sb.table("rph").select(FETCH_COLS).in_("registration_number", chunk).execute().data or []
        for row in rows:
            current[row["registration_number"]] = row
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill blank rph columns from the live source")
    parser.add_argument("--ids-file", required=True, help="JSONL with registration_number (identity fields optional)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-delay", type=float, default=0.5)
    args = parser.parse_args()

    path = Path(args.ids_file)
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    regs = [r["registration_number"] for r in records if r.get("registration_number")]
    if not regs:
        print("no records in ids file", file=sys.stderr)
        return 1
    expected = {r["registration_number"]: r for r in records if r.get("registration_number")}
    print(f"loaded {len(regs)} ids from {path}")

    sb = _supabase()
    current = _fetch_current(sb, regs)

    lock = threading.Lock()
    stats = {"filled": 0, "unchanged": 0, "not_found": 0, "mismatch": 0, "error": 0, "fields": 0}
    start = time.time()

    def make_scraper():
        sc = Scraper()
        sc.rate_limiter.min_delay = args.min_delay
        sc.rate_limiter.current_delay = args.min_delay
        return sc

    def work(sc, reg_no):
        db_row = current.get(reg_no)
        if db_row is None:
            with lock:
                stats["error"] += 1
            step(f"{reg_no}: not in DB — skipped")
            return
        try:
            details = sc.extract_detailed_info(reg_no)
            if not details:
                with lock:
                    stats["not_found"] += 1
                step(f"{reg_no}: NOT FOUND on source")
                return

            # Identity guard: never patch a record the source resolves to
            # somebody else (protects against renumbered registration IDs).
            exp = (expected.get(reg_no) or {}).get("name") or db_row.get("name") or ""
            if details.name and exp and details.name.strip().lower() != exp.strip().lower():
                with lock:
                    stats["mismatch"] += 1
                step(f"{reg_no}: NAME MISMATCH (db={exp!r} source={details.name!r}) — skipped")
                return

            patch = {}
            for col in FILLABLE:
                if _blank(db_row.get(col)):
                    value = getattr(details, col, None)
                    if not _blank(value):
                        patch[col] = value.strip()
            if not patch:
                with lock:
                    stats["unchanged"] += 1
                step(f"{reg_no}: nothing to fill")
                return

            sb.table("rph").update(patch).eq("registration_number", reg_no).execute()
            with lock:
                stats["filled"] += 1
                stats["fields"] += len(patch)
            step(f"{reg_no}: filled {sorted(patch)}")
        except Exception as e:  # noqa: BLE001 - one bad record must not stop the batch
            with lock:
                stats["error"] += 1
            step(f"{reg_no}: ERROR {e}")
            print(f"ERROR {reg_no}: {e}", file=sys.stderr)

    with ProgressBar(total=len(regs), label="Backfilling blanks", cadence=1) as bar:
        if args.workers <= 1:
            sc = make_scraper()
            for reg in regs:
                bar.set_detail(reg)
                work(sc, reg)
                bar.update(1, detail=reg)
        else:

            def worker(chunk):
                sc = make_scraper()
                for reg in chunk:
                    bar.set_detail(reg)
                    work(sc, reg)
                    bar.update(1, detail=reg)

            chunks = [regs[i :: args.workers] for i in range(args.workers)]
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                list(pool.map(worker, chunks))

    elapsed = time.time() - start
    print(f"\nBackfill complete in {elapsed:.0f}s ({len(regs) / max(elapsed, 1):.1f}/s)")
    print(f"Stats: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
