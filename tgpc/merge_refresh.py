"""
Post-scrape refresh of DG-merged columns on `public.rph`.

Runs automatically at the end of `python3 -m tgpc update` (Phase 4/4). The
three dashboard backfills (date_of_registration, home_state, validity
newer-wins) are idempotent by construction, so this recomputes them from the
live tables every run and only upserts rows that still need it — usually zero.

Deliberately warn-and-continue on failure (derived data, recomputable next
run): a refresh hiccup must never fail a scrape. Missing credentials fail
closed with an error stat instead of raising.

Takes the Manager as first argument and builds the client through
`manager._make_supabase_client()`, so tests keep patching
`tgpc.manager.create_client` like every other sync seam.
"""

import os
from datetime import datetime

from tgpc.progress import step
from tgpc.utils import setup_logging


logger = setup_logging("tgpc.merge_refresh")

DG_TABLE = "rph_dg_contacts"
BASE_TABLE = "rph"
PAGE = 2000
BATCH = 500
MIN_YEAR = 1900
MAX_YEAR = 2032

DG_COLS = "registration_number,date_of_registration,home_state,renewal_validity"
RPH_COLS = "registration_number,date_of_registration,home_state,validity_date"


def _fetch_all(supabase, table, cols):
    """Paginate a narrow column slice (PostgREST caps pages at 1000 rows)."""
    rows, start = [], 0
    while True:
        page = (
            supabase.table(table)
            .select(cols)
            .order("registration_number")
            .range(start, start + PAGE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        start += PAGE


def _parse_dmy(value):
    """Strict DD-MM-YYYY -> date, or None (never raises on dirty data)."""
    try:
        return datetime.strptime(value or "", "%d-%m-%Y").date()
    except (ValueError, TypeError):
        return None


def _parse_mon(value):
    """Strict DD-Mon-YYYY -> date, or None."""
    try:
        return datetime.strptime(value or "", "%d-%b-%Y").date()
    except (ValueError, TypeError):
        return None


def refresh_merged_columns(manager):
    """Recompute merged columns from the live tables. Returns a stats dict.

    The dict always carries integer counts (dor_filled, home_filled,
    validity_updated, review_skipped) and an `error` key that is "" on
    success. Never raises for data problems — unparseable rows are counted
    in review_skipped and left untouched.
    """
    stats = {
        "dor_filled": 0,
        "home_filled": 0,
        "validity_updated": 0,
        "review_skipped": 0,
        "error": "",
    }
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        stats["error"] = "missing SUPABASE_URL/SECRET_KEY"
        logger.error("Merge refresh skipped: %s", stats["error"])
        return stats

    try:
        supabase = manager._make_supabase_client()
        dg_rows = _fetch_all(supabase, DG_TABLE, DG_COLS)
        rph_rows = _fetch_all(supabase, BASE_TABLE, RPH_COLS)
    except Exception as e:
        stats["error"] = f"fetch failed: {e}"
        logger.error("Merge refresh skipped: %s", stats["error"])
        return stats

    dg = {r["registration_number"]: r for r in dg_rows}
    updates = []
    for row in rph_rows:
        reg = row.get("registration_number", "")
        src = dg.get(reg)
        if not src:
            continue
        patch = {"registration_number": reg}
        dor = (row.get("date_of_registration") or "").strip()
        if not dor and (src.get("date_of_registration") or "").strip():
            patch["date_of_registration"] = src["date_of_registration"].strip()
            stats["dor_filled"] += 1
        hs = (row.get("home_state") or "").strip()
        dhs = (src.get("home_state") or "").strip()
        if not hs and dhs:
            patch["home_state"] = dhs.upper()
            stats["home_filled"] += 1
        rv = _parse_dmy((src.get("renewal_validity") or "").strip())
        if rv is not None and MIN_YEAR <= rv.year <= MAX_YEAR:
            cur_raw = (row.get("validity_date") or "").strip()
            if not cur_raw:
                patch["validity_date"] = rv.strftime("%d-%b-%Y")
                stats["validity_updated"] += 1
            else:
                cur = _parse_mon(cur_raw)
                if cur is None:
                    stats["review_skipped"] += 1
                elif rv > cur:
                    patch["validity_date"] = rv.strftime("%d-%b-%Y")
                    stats["validity_updated"] += 1
        if len(patch) > 1:
            updates.append(patch)

    try:
        for i in range(0, len(updates), BATCH):
            batch = updates[i : i + BATCH]
            step(f"refreshing merged columns batch {i // BATCH + 1}")
            supabase.table(BASE_TABLE).upsert(batch, on_conflict="registration_number").execute()
    except Exception as e:
        stats["error"] = f"upsert failed after {stats['validity_updated']} validity updates: {e}"
        logger.error("Merge refresh partial: %s", stats["error"])
        return stats

    logger.info(
        "Merge refresh: dor=%d home=%d validity=%d review=%d",
        stats["dor_filled"],
        stats["home_filled"],
        stats["validity_updated"],
        stats["review_skipped"],
    )
    return stats
