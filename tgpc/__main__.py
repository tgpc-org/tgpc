"""
CLI entry point for TGPC system.
"""

import argparse
import atexit
import getpass
import os
import subprocess
import sys
import time

from tgpc.manager import Manager
from tgpc.progress import Phase
from tgpc.quota import show_quotas
from tgpc.run_status import egress_ip, send_alert, write_run_status
from tgpc.utils import (
    KEYCHAIN_SERVICE,
    CREDENTIAL_KEYS,
    _get_keychain,
    _set_keychain,
    warp_cli_run,
)


# --- Cloudflare WARP ---

_warp_connected_by_us = False


def _warp_available() -> bool:
    """Check if warp-cli is installed and reachable."""
    try:
        r = warp_cli_run("status", timeout=5)
        return r.returncode == 0 or "Connected" in r.stdout or "Disconnected" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _warp_connect() -> bool:
    """Connect Cloudflare WARP. Returns True only if THIS call established
    the connection (so the atexit handler knows it may disconnect).
    Returns False when already connected — a pre-existing user connection
    must never be torn down on exit."""
    if not _warp_available():
        return False

    try:
        r = warp_cli_run("status", timeout=5)
        if "Connected" in r.stdout:
            print("WARP: already connected (leaving it up on exit)")
            return False

        r = warp_cli_run("connect", timeout=15)
        if r.returncode == 0:
            print("WARP: connected")
            return True

        print(f"WARP: connect failed — {r.stderr.strip() or r.stdout.strip()}")
        return False
    except Exception as e:
        print(f"WARP: connect error — {e}")
        return False


def _warp_disconnect():
    """Disconnect Cloudflare WARP."""
    if not _warp_available():
        return

    try:
        r = warp_cli_run("disconnect", timeout=15)
        if r.returncode == 0:
            print("WARP: disconnected")
        else:
            print(f"WARP: disconnect failed — {r.stderr.strip() or r.stdout.strip()}")
    except Exception as e:
        print(f"WARP: disconnect error — {e}")


def _warp_ensure_disconnected():
    """Disconnect WARP on exit, but only if this process established the
    connection. Registered with atexit."""
    global _warp_connected_by_us
    if _warp_connected_by_us:
        _warp_disconnect()
        _warp_connected_by_us = False


# --- Pipeline steps shared by `update` and `scrape` ---


def _run_update(manager, args) -> str:
    """Run the daily update and every follow-on step; returns the status.

    Kept as a function so `update` and `scrape` share one implementation —
    `scrape` must run this and dispatch inside a single process, otherwise the
    WARP tunnel is torn down at the first process's exit.
    """
    with Phase("Daily update", 1, 4):
        status = manager.run_daily_update(force=args.force)
    if status in ("source_unavailable", "blocked"):
        print(f"TGPC source {status} — no new data to sync.")
        return status
    if status == "updated":
        if not args.no_sync:
            with Phase("Sync to cloud destinations", 2, 4):
                all_records = manager.file_manager.load()
                new_regs: set[str] = getattr(manager, "_last_new_regs", set())
                mod_regs: set[str] = getattr(manager, "_last_modified_regs", set())
                removed_regs: set[str] = getattr(manager, "_last_removed_regs", set())
                delta_ids = new_regs | set(mod_regs)
                delta = [r for r in all_records if r.registration_number in delta_ids] if delta_ids else []
                sync_results = [manager.sync_to_supabase(delta_records=delta)]
                # Delete orphans that were removed from source
                if removed_regs:
                    sync_results.append(manager.delete_removed_from_supabase(removed_regs))
                # Any change — new, modified, OR removed — must propagate
                # to the file destinations and the email report. A
                # removals-only update has empty delta_ids, so gate on the
                # union including removals.
                if delta_ids or removed_regs:
                    sync_results += [
                        manager.sync_to_supabase_storage(),
                        manager.sync_to_r2(),
                        manager.sync_to_gdrive(),
                        manager.sync_to_release(),
                        manager.sync_to_email(),
                    ]
                else:
                    print("No changes — skipping full syncs.")
                if not all(sync_results):
                    print("One or more sync destinations failed", file=sys.stderr)
                    raise SystemExit(1)
            if new_regs:
                with Phase("Enrich new records", 3, 4):
                    print(f"Enriching {len(new_regs)} new records...")
                    manager.enrich_new_records(force=args.force)
            if not args.no_sync:
                with Phase("Refresh merged columns", 4, 4):
                    stats = manager.refresh_merged_columns()
                    if stats.get("error"):
                        print(f"Merge refresh warning: {stats['error']} (retry next run)", file=sys.stderr)
                    else:
                        print(
                            "Merge refresh: "
                            f"dor={stats['dor_filled']} home={stats['home_filled']} "
                            f"validity={stats['validity_updated']} review={stats['review_skipped']}"
                        )
    return status


def _run_dispatch(
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
    write_fallback: bool = False,
) -> None:
    """Copy newly published council dispatch PDFs into R2."""
    from tgpc.dispatch import sync_dispatch

    with Phase("Sync dispatch lists", 1, 1):
        report = sync_dispatch(
            dry_run=dry_run,
            force=force,
            limit=limit,
            write_fallback=write_fallback,
        )
    print(
        f"Dispatch lists: {report.published} published, {report.already_present} already in R2, "
        f"{report.missing} missing, {report.uploaded} uploaded, {report.failed} failed "
        f"(newest {report.newest or 'none'})"
    )
    if report.failed_names:
        print(f"  failed: {', '.join(report.failed_names)}", file=sys.stderr)
        raise SystemExit(1)


# Human-readable detail for each non-success status. The log has the specifics;
# this is what lands in data/last_run.json and in the alert email.
_STATUS_DETAIL = {
    "blocked": "source preflight failed — the source is blocked or unreachable from this host",
    "source_unavailable": "source error during scrape — existing data kept, nothing synced",
    "safety_abort": "record count dropped more than 10% — refusing to overwrite good data",
    "empty_scrape": "scrape returned no records — existing data kept",
    "restore_failed": "data/rph.json is missing and the R2 backup restore failed",
    "sync_failed": "a cloud destination failed — see the run log",
    "dispatch_failed": "a dispatch list failed to upload — see the run log",
}


def _finish_run(
    status: str,
    started: float,
    exit_code: int = 2,
    host_egress: str = "",
    command: str = "scrape",
) -> None:
    """Record the outcome, alert if it was not a success, and fail loudly.

    Silence was the problem: a blocked source used to log one line and exit 0,
    so a scheduled run could fail for weeks unnoticed. `data/last_run.json` is
    what the scheduler reads (to skip a day that already succeeded), the email
    is what a human reads, and the non-zero exit is what any wrapper sees.
    """
    record = write_run_status(
        status,
        error=_STATUS_DETAIL.get(status, "") if status != "updated" else "",
        duration_s=round(time.monotonic() - started, 1),
        egress=egress_ip(),
        host_egress=host_egress,
        command=command,
    )
    if status == "updated":
        return
    print(f"Run status: {status} — {_STATUS_DETAIL.get(status, '')}", file=sys.stderr)
    send_alert(record)
    raise SystemExit(exit_code)


# --- Credential CLI commands ---


def _cmd_creds_set(args):
    if args.key_value and "=" in args.key_value:
        key, value = args.key_value.split("=", 1)
        _set_keychain(key, value)
        print(f"Stored {key} in Keychain")
        return
    for key in CREDENTIAL_KEYS:
        existing = _get_keychain(key)
        if existing and not args.force:
            print(f"{key} — already set (use --force to overwrite)")
            continue
        prompt = f"{key}" + (f" [{existing[:8]}...]" if existing else "")
        value = getpass.getpass(f"{prompt}: ")
        if value:
            _set_keychain(key, value)
            print(f"  ✓ {key} stored")
        else:
            print(f"  - {key} skipped")


def _cmd_creds_list(_args):
    for key in CREDENTIAL_KEYS:
        env = os.environ.get(key, "")
        kc = _get_keychain(key)
        if env:
            print(f"{key}  env: {env[:12]}...")
        elif kc:
            print(f"{key}  keychain: {kc[:12]}...")
        else:
            print(f"{key}  — not set")


def _cmd_creds_delete(args):
    existing = _get_keychain(args.key)
    if existing:
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", args.key],
            capture_output=True,
            check=True,
        )
        print(f"Deleted {args.key} from Keychain")
    else:
        print(f"{args.key} not found in Keychain")


def _cmd_dg_pending(args):
    """Print (and optionally write) the DG registration numbers still to fetch."""
    import json
    from pathlib import Path

    from tgpc.details_dg import pending_ids

    reference = Path(args.reference)
    checkpoint_path = Path(args.checkpoint)
    try:
        rows = json.loads(reference.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Cannot read {reference}: {e}", file=sys.stderr)
        raise SystemExit(1)
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        checkpoint = {}

    ids = pending_ids(
        rows,
        checkpoint,
        retry_terminal=args.retry_terminal,
        limit=args.limit,
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")
    print(f"{len(ids)} pending")
    for reg in ids:
        print(reg)


def main():
    parser = argparse.ArgumentParser(description="TGPC RPh Index Manager")
    subparsers = parser.add_subparsers(dest="command")

    # Update command
    update_parser = subparsers.add_parser("update", help="Run daily update process")
    update_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip sync to cloud destinations after update",
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="Override safety caps (e.g. enrich >1000 records)",
    )

    # Scrape command: update + dispatch in ONE process so the WARP tunnel
    # opened for the scrape also covers the dispatch download and is closed
    # only once, at the end of the run.
    scrape_parser = subparsers.add_parser(
        "scrape", help="Full pipeline: update + sync + enrich + dispatch in one process"
    )
    scrape_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip sync to cloud destinations after update",
    )
    scrape_parser.add_argument(
        "--force",
        action="store_true",
        help="Override update safety caps (e.g. enrich >1000 records)",
    )

    # Sync command
    subparsers.add_parser("sync", help="Sync rph.json to all cloud destinations")

    # Enrich command
    enrich_parser = subparsers.add_parser("enrich", help="Upload photos for new records from last scrape")
    enrich_parser.add_argument(
        "--force",
        action="store_true",
        help="Override safety caps (e.g. enrich >1000 records)",
    )

    # Retry-photos command
    subparsers.add_parser("retry-photos", help="Retry uploading failed photos from data/webp/ to R2")

    # Dispatch command (council dispatch-list PDFs -> R2)
    dispatch_parser = subparsers.add_parser("dispatch", help="Sync new dispatch-list PDFs from the council into R2")
    dispatch_parser.add_argument("--dry-run", action="store_true", help="List what is missing without downloading")
    dispatch_parser.add_argument(
        "--force", action="store_true", help="Re-upload every published file, not just the missing ones"
    )
    dispatch_parser.add_argument("--limit", type=int, default=None, help="Upload at most N files this run")
    dispatch_parser.add_argument(
        "--write-fallback",
        action="store_true",
        help="Regenerate the listing route's stale FALLBACK_FILES from the bucket",
    )

    # Validate-dg command (offline: re-check L1 contacts, no website contact)
    validate_dg_parser = subparsers.add_parser("validate-dg", help="Offline validation pass over dg_contacts.jsonl")
    validate_dg_parser.add_argument("--out", default="data/dg_contacts.jsonl", help="Contacts jsonl to validate")
    validate_dg_parser.add_argument("--raw-dir", default="data/dg_raw", help="Per-record raw snapshot dir")
    validate_dg_parser.add_argument("--max-list", type=int, default=50, help="Max flagged IDs to list")

    # Dg-pending command: which registration numbers are still worth fetching.
    # The DG fetch now runs on the operator's Mac, so the selection rule that
    # decides what gets re-requested needs a first-class command (it used to
    # be a shell heredoc inside the VM's fetch script).
    dg_pending_parser = subparsers.add_parser("dg-pending", help="List registration numbers still worth fetching (DG)")
    dg_pending_parser.add_argument("--reference", default="data/rph.json", help="rph.json master list")
    dg_pending_parser.add_argument("--checkpoint", default="data/dg_fetch_checkpoint.json", help="DG fetch checkpoint")
    dg_pending_parser.add_argument("--out", default="", help="Write the IDs here (default: stdout only)")
    dg_pending_parser.add_argument("--limit", type=int, default=None, help="Max IDs (0 = all)")
    dg_pending_parser.add_argument(
        "--retry-terminal",
        action="store_true",
        help="Also re-attempt terminal failures (explicit re-probe of the whole set)",
    )

    # Fetch-dg command (getdetailsdg captcha flow: capture + save contact details)
    fetch_dg_parser = subparsers.add_parser("fetch-dg", help="Fetch getdetailsdg records with captcha")
    fetch_dg_parser.add_argument("--ids", nargs="*", default=[], help="Registration IDs (e.g. TS003261)")
    fetch_dg_parser.add_argument("--ids-file", default=None, help="File with one reg ID per line")
    fetch_dg_parser.add_argument("--out", default="data/dg_contacts.jsonl", help="Streamlined jsonl output")
    fetch_dg_parser.add_argument("--raw-dir", default="data/dg_raw", help="Per-record raw snapshot dir")
    fetch_dg_parser.add_argument("--checkpoint", default="data/dg_fetch_checkpoint.json")
    fetch_dg_parser.add_argument("--stats", default="data/dg_stats.json")
    fetch_dg_parser.add_argument("--reference", default="data/rph.json", help="rph.json for serial numbers")
    fetch_dg_parser.add_argument("--min-delay", type=float, default=3.0)
    fetch_dg_parser.add_argument("--max-captcha-attempts", type=int, default=1)
    fetch_dg_parser.add_argument("--no-resume", action="store_true", help="Ignore existing checkpoint")
    fetch_dg_parser.add_argument(
        "--allow-clobber",
        action="store_true",
        help="Allow --no-resume to overwrite the production checkpoint (otherwise refused)",
    )
    fetch_dg_parser.add_argument(
        "--retry-terminal", action="store_true", help="Re-attempt terminal failures (not-authorized/not-found)"
    )
    fetch_dg_parser.add_argument(
        "--captcha", choices=["auto", "manual"], default="auto", help="auto=OCR, manual=prompt per record"
    )
    fetch_dg_parser.add_argument("--push-r2", action="store_true", help="Best-effort R2 push of artifacts")
    fetch_dg_parser.add_argument(
        "--sync-cloud",
        action="store_true",
        help="Upsert to rph_dg_contacts + push R2/GDrive/SB snapshots (needs dg_migration.sql)",
    )
    fetch_dg_parser.add_argument("--sync-every", type=int, default=50, help="Supabase batch size for --sync-cloud")
    fetch_dg_parser.add_argument("--max-records", type=int, default=None, help="Stop after N newly processed records")
    fetch_dg_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Fetch pool size (default: TGPC_DG_WORKERS or 4; max 16 — raise in steps, site filters aggressively)",
    )
    fetch_dg_parser.add_argument(
        "--max-consecutive-blocks",
        type=int,
        default=25,
        help="Circuit breaker: halt after N consecutive BlockedErrors (0 disables; terminal gaps/saves reset)",
    )
    fetch_dg_parser.add_argument(
        "--max-transient-retries",
        type=int,
        default=5,
        help="Per-record transient retry cap across runs; excess marked terminal (retry-exhausted), 0 = unlimited",
    )
    fetch_dg_parser.add_argument(
        "--warp-rotate-every",
        type=int,
        default=0,
        help="Verified new egress IP every N records (0=off); halts if IP won't rotate",
    )
    fetch_dg_parser.add_argument(
        "--warp-max-cycles", type=int, default=3, help="WARP reconnect tries per rotation gate"
    )

    # Quota command
    subparsers.add_parser("quota", help="Show free quota usage for all services")

    # Creds command
    creds_parser = subparsers.add_parser("creds", help="Manage credentials in macOS Keychain")
    creds_sub = creds_parser.add_subparsers(dest="creds_cmd")

    set_p = creds_sub.add_parser("set", help="Store credentials interactively or via KEY=VALUE")
    set_p.add_argument("key_value", nargs="?", help="KEY=VALUE pair (omit for interactive)")
    set_p.add_argument("--force", "-f", action="store_true", help="Overwrite existing values")

    creds_sub.add_parser("list", help="Show which credentials are set")

    del_p = creds_sub.add_parser("delete", help="Delete a credential from Keychain")
    del_p.add_argument("key", choices=CREDENTIAL_KEYS, help="Credential key to delete")

    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return

    if args.command == "creds":
        if not args.creds_cmd:
            creds_parser.print_help()
            return
        if args.creds_cmd == "set":
            _cmd_creds_set(args)
        elif args.creds_cmd == "list":
            _cmd_creds_list(args)
        elif args.creds_cmd == "delete":
            _cmd_creds_delete(args)
        return

    if args.command == "dg-pending":
        _cmd_dg_pending(args)
        return

    manager = Manager()

    # Sample the host's own address BEFORE any tunnel is raised: once WARP is
    # up, the observed egress is Cloudflare's, which says nothing about the
    # ISP lease. This is the number that answers "has my IP changed?".
    host_egress = egress_ip() if args.command in ("update", "scrape") else ""

    # Connect WARP for network-level routing (update and sync hit external
    # services). `scrape` is one process so its WARP connection covers both
    # update and dispatch and is torn down only when the whole run ends.
    global _warp_connected_by_us
    if args.command in ("update", "scrape", "sync", "enrich", "retry-photos", "fetch-dg"):
        atexit.register(_warp_ensure_disconnected)
        _warp_connected_by_us = _warp_connect()

    # Command dispatch. WARP disconnect on exit is handled solely by the
    # atexit handler registered above (_warp_ensure_disconnected), which also
    # covers SystemExit / early returns. A redundant try/finally here was
    # removed (CODE_REVIEW.md L6).
    if args.command in ("update", "scrape"):
        started = time.monotonic()
        try:
            status = _run_update(manager, args)
        except SystemExit:
            # A failed destination aborts the run (exit 1). Record it before
            # propagating so the scheduler and the alert see the same outcome.
            _finish_run("sync_failed", started, exit_code=1, host_egress=host_egress, command=args.command)
            raise
        # Dispatch runs after a successful update in the SAME process, so the
        # WARP tunnel stays up instead of being torn down between two commands.
        if args.command == "scrape":
            try:
                _run_dispatch()
            except SystemExit:
                _finish_run("dispatch_failed", started, exit_code=1, host_egress=host_egress, command=args.command)
                raise
        _finish_run(status, started, host_egress=host_egress, command=args.command)
        return
    elif args.command == "sync":
        with Phase("Sync to cloud destinations", 1, 1):
            sync_results = [
                manager.sync_to_supabase(),
                manager.sync_to_supabase_storage(),
                manager.sync_to_r2(),
                manager.sync_to_gdrive(),
                manager.sync_to_release(),
                manager.sync_to_email(),
            ]
            if not all(sync_results):
                print("One or more sync destinations failed", file=sys.stderr)
                raise SystemExit(1)
    elif args.command == "enrich":
        with Phase("Enrich new records", 1, 1):
            manager.enrich_new_records(force=args.force)
    elif args.command == "retry-photos":
        with Phase("Retry failed photos", 1, 1):
            manager.retry_photos()
    elif args.command == "fetch-dg":
        import json
        from pathlib import Path

        from tgpc.details_dg import CaptchaNeeded, run_fetch
        from tgpc.utils import load_credentials

        load_credentials()  # keychain -> env (R2/GDrive/Supabase), same as Manager

        ids = list(args.ids or [])
        if args.ids_file:
            ids += [ln.strip() for ln in Path(args.ids_file).read_text().splitlines() if ln.strip()]
        if not ids:
            print("fetch-dg: no IDs (pass --ids or --ids-file)", file=sys.stderr)
            raise SystemExit(2)
        checkpoint_path = Path(args.checkpoint)
        if args.no_resume and checkpoint_path.exists() and not args.allow_clobber:
            print(
                f"fetch-dg: refusing --no-resume on existing checkpoint {checkpoint_path} "
                "(would wipe resume state; pass --allow-clobber to confirm)",
                file=sys.stderr,
            )
            raise SystemExit(2)
        reference = None
        ref_path = Path(args.reference)
        if ref_path.exists():
            try:
                rows = json.loads(ref_path.read_text())
                reference = {r.get("registration_number", "").upper(): r for r in rows if r.get("registration_number")}
                print(f"fetch-dg: identity guard vs {len(reference)} reference rows")
            except Exception as e:
                print(f"fetch-dg: ignoring unreadable reference ({e})", file=sys.stderr)
        solver = None
        if args.workers is not None:
            import tgpc.details_dg as _dg

            _dg.DG_WORKERS = min(max(args.workers, 1), 16)
            print(f"fetch-dg: workers={_dg.DG_WORKERS} (override)")
        if args.captcha == "manual":

            def solver(image_bytes: bytes) -> str:  # noqa: F811
                tmp = Path("data/dg_captcha_tmp.jpg")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(image_bytes)
                try:
                    code = input(f"Captcha saved to {tmp} — enter code: ")
                except EOFError as e:
                    raise CaptchaNeeded("no TTY for manual captcha") from e
                code = "".join(code.split()).upper()
                if not code:
                    raise CaptchaNeeded("empty manual captcha")
                return code

        with Phase(f"Fetch {len(ids)} getdetailsdg record(s)", 1, 1):
            stats = run_fetch(
                ids,
                out_jsonl=Path(args.out),
                raw_dir=Path(args.raw_dir),
                checkpoint_path=checkpoint_path,
                stats_path=Path(args.stats),
                reference=reference,
                min_delay=args.min_delay,
                max_captcha_attempts=args.max_captcha_attempts,
                resume=not args.no_resume,
                retry_terminal=args.retry_terminal,
                captcha_solver=solver,
                push_r2=args.push_r2,
                sync_cloud=args.sync_cloud,
                sync_every=args.sync_every,
                max_records=args.max_records,
                warp_rotate_every=args.warp_rotate_every,
                warp_max_cycles=args.warp_max_cycles,
                max_consecutive_blocks=args.max_consecutive_blocks,
                max_transient_retries=args.max_transient_retries,
            )
        print(json.dumps(stats, indent=2))
    elif args.command == "validate-dg":
        import json
        from pathlib import Path

        from tgpc.details_dg import validate_l1

        stats = validate_l1(Path(args.out), Path(args.raw_dir), max_list=args.max_list)
        print(json.dumps(stats, indent=2))
    elif args.command == "dispatch":
        _run_dispatch(
            dry_run=args.dry_run,
            force=args.force,
            limit=args.limit,
            write_fallback=args.write_fallback,
        )
    elif args.command == "quota":
        with Phase("Show service quotas", 1, 1):
            show_quotas()


if __name__ == "__main__":
    main()
