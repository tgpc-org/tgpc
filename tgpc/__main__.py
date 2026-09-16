"""
CLI entry point for TGPC system.
"""

import argparse
import atexit
import getpass
import os
import subprocess
import sys
from tgpc.manager import Manager
from tgpc.progress import Phase
from tgpc.quota import show_quotas
from tgpc.utils import (
    KEYCHAIN_SERVICE,
    CREDENTIAL_KEYS,
    _get_keychain,
    _set_keychain,
)


# --- Cloudflare WARP ---

_warp_connected_by_us = False


def _warp_available() -> bool:
    """Check if warp-cli is installed and reachable."""
    try:
        r = subprocess.run(["warp-cli", "status"], capture_output=True, text=True, timeout=5)
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
        r = subprocess.run(["warp-cli", "status"], capture_output=True, text=True, timeout=5)
        if "Connected" in r.stdout:
            print("WARP: already connected (leaving it up on exit)")
            return False

        r = subprocess.run(["warp-cli", "connect"], capture_output=True, text=True, timeout=15)
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
        r = subprocess.run(["warp-cli", "disconnect"], capture_output=True, text=True, timeout=15)
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

    manager = Manager()

    # Connect WARP for network-level routing (update and sync hit external services)
    global _warp_connected_by_us
    if args.command in ("update", "sync", "enrich", "retry-photos"):
        atexit.register(_warp_ensure_disconnected)
        _warp_connected_by_us = _warp_connect()

    # Command dispatch. WARP disconnect on exit is handled solely by the
    # atexit handler registered above (_warp_ensure_disconnected), which also
    # covers SystemExit / early returns. A redundant try/finally here was
    # removed (CODE_REVIEW.md L6).
    if args.command == "update":
        with Phase("Daily update", 1, 4):
            status = manager.run_daily_update(force=args.force)
        if status in ("source_unavailable", "blocked"):
            print(f"TGPC source {status} — no new data to sync.")
            return
        if status == "updated":
            if not args.no_sync:
                with Phase("Sync to cloud destinations", 2, 4):
                    all_records = manager.file_manager.load()
                    new_regs = getattr(manager, "_last_new_regs", set())
                    mod_regs = getattr(manager, "_last_modified_regs", set())
                    removed_regs = getattr(manager, "_last_removed_regs", set())
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
    elif args.command == "quota":
        with Phase("Show service quotas", 1, 1):
            show_quotas()


if __name__ == "__main__":
    main()
