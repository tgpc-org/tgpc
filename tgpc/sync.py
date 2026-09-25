"""
Cloud sync destinations for TGPC: Supabase (DB + Storage), Cloudflare R2,
Google Drive, GitHub Release, and the Resend report email.

Split out of manager.py so the orchestration module stays focused. Every
function takes the Manager as its first argument and resolves credentials
from the environment / manager factories, so tests can keep patching
tgpc.manager.create_client, tgpc.manager.requests.post, and
tgpc.manager.subprocess.run.
"""

import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from pathlib import Path

import requests

from tgpc.progress import heartbeat, ProgressBar
from tgpc.utils import setup_logging


logger = setup_logging("tgpc.sync")


def sync_to_supabase(manager, delta_records=None):
    """Sync data to Supabase. If delta_records provided, only upsert those.

    Returns True on success, False on failure so callers can propagate it.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")

    if not url or not key:
        logger.error("Missing Supabase credentials")
        return False

    try:
        supabase = manager._make_supabase_client()

        if delta_records is not None:
            records = delta_records
        else:
            records = manager.file_manager.load()

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

        # Post-sync verification: compare counts. head=True keeps this a
        # cheap COUNT query instead of fetching every row.
        local_records = manager.file_manager.load()
        local_count = len(local_records)
        try:
            result = supabase.table("rph").select("registration_number", count="exact", head=True).execute()
            remote_count = result.count
            if local_count != remote_count:
                logger.warning(
                    f"Supabase mismatch detected (local={local_count}, remote={remote_count}). "
                    f"Running full sync to fix..."
                )
                all_records = local_records
                fix_batches = (len(all_records) + batch_size - 1) // batch_size
                with ProgressBar(total=fix_batches, label="Full re-sync to Supabase") as bar:
                    for i in range(0, len(all_records), batch_size):
                        batch = [r.to_dict() for r in all_records[i : i + batch_size]]
                        supabase.table("rph").upsert(batch, on_conflict="registration_number").execute()
                        bar.update(1, detail=f"batch {i // batch_size + 1}/{fix_batches}")
                result = supabase.table("rph").select("registration_number", count="exact", head=True).execute()
                remote_count = result.count
                if local_count == remote_count:
                    logger.info(f"Full sync fixed mismatch. Supabase now has {remote_count} records")
                elif remote_count is not None and remote_count > local_count:
                    # Upsert-only repair cannot remove remote orphans —
                    # delete them explicitly so the drift actually heals.
                    delete_supabase_orphans(supabase, {r.registration_number for r in all_records})
                    result = supabase.table("rph").select("registration_number", count="exact", head=True).execute()
                    remote_count = result.count
                    if local_count == remote_count:
                        logger.info(f"Orphan cleanup fixed mismatch. Supabase now has {remote_count} records")
                    else:
                        logger.critical(
                            f"SUPABASE STILL MISMATCHED after full sync + orphan cleanup: "
                            f"local={local_count}, remote={remote_count}"
                        )
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


def delete_supabase_orphans(supabase, local_ids: set) -> bool:
    """Delete remote rows whose registration_number is not in local_ids.

    Only called after an upsert-only repair still leaves remote ahead of
    local, so the count drift actually heals instead of logging CRITICAL
    on every run forever.
    """
    try:
        orphans = []
        page = 1000
        start = 0
        while True:
            resp = (
                supabase.table("rph")
                .select("registration_number")
                .order("registration_number")
                .range(start, start + page - 1)
                .execute()
            )
            rows = resp.data or []
            if not rows:
                break
            orphans.extend(r["registration_number"] for r in rows if r["registration_number"] not in local_ids)
            if len(rows) < page:
                break
            start += page
        if not orphans:
            logger.info("Orphan scan found no extra remote rows")
            return True
        batch = 500
        for i in range(0, len(orphans), batch):
            supabase.table("rph").delete().in_("registration_number", orphans[i : i + batch]).execute()
        logger.info(f"Deleted {len(orphans)} orphan rows from Supabase")
        return True
    except Exception as e:
        logger.error(f"Orphan cleanup failed: {e}")
        return False


def delete_removed_from_supabase(manager, removed_ids: set) -> bool:
    """Delete removed records from Supabase."""
    if not removed_ids:
        return True
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        return False
    try:
        supabase = manager._make_supabase_client()
        # Batch delete (Supabase IN limit)
        ids = list(removed_ids)
        batch = 500
        for i in range(0, len(ids), batch):
            supabase.table("rph").delete().in_("registration_number", ids[i : i + batch]).execute()
        logger.info(f"Deleted {len(ids)} removed records from Supabase")
        return True
    except Exception as e:
        logger.error(f"Delete failed: {e}")
        return False


def sync_to_supabase_storage(manager):
    """Upload rph.json to Supabase Storage (tgpc bucket)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        logger.error("Missing Supabase credentials")
        return False
    file_path = manager.file_manager.data_dir / "rph.json"
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


def sync_to_r2(manager):
    """Sync rph.json to Cloudflare R2. Photos are uploaded during enrichment.

    Returns True on success, False on failure so callers can propagate it.
    """
    endpoint = manager._get_r2_endpoint()
    if not endpoint:
        logger.error("Missing CLOUDFLARE_ACCOUNT_ID for R2 sync")
        return False

    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not all([access_key, secret_key]):
        logger.error("Missing R2 credentials")
        return False

    file_path = manager.file_manager.data_dir / "rph.json"
    client = manager._make_r2_client(endpoint, access_key, secret_key)

    # Upload rph.json
    with heartbeat("Uploading rph.json to R2"):
        try:
            client.put_object("rph.json", file_path)
            logger.info("R2 rph.json sync complete")
            return True
        except Exception as e:
            logger.error(f"R2 rph.json sync error: {e}")
            return False


def sync_to_gdrive(manager):
    """Sync rph.json to Google Drive via rclone.

    Returns True on success, False on failure so callers can propagate it.
    """
    gdrive_config_b64 = os.environ.get("RCLONE_GDRIVE_CONFIG")
    if not gdrive_config_b64:
        logger.error("Missing RCLONE_GDRIVE_CONFIG")
        return False

    with heartbeat("Syncing rph.json to Google Drive"):
        try:
            import base64
            import tempfile

            # Unique, unpredictable temp path — never a fixed /tmp name a
            # planted symlink or concurrent run could redirect or read
            # (audit lead: FINGERPRINT-rclone-config-tmpfile).
            with tempfile.NamedTemporaryFile(prefix="rclone-gdrive-", suffix=".conf", delete=False) as tmp:
                tmp.write(base64.b64decode(gdrive_config_b64))
            config_path = Path(tmp.name)
            try:
                result = subprocess.run(
                    [
                        "rclone",
                        "copyto",
                        str(manager.file_manager.data_dir / "rph.json"),
                        "gdrive:tgpc/rph.json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env={**os.environ, "RCLONE_CONFIG": str(config_path)},
                )
            finally:
                # Never leave the decoded service-account config behind,
                # even on timeout/crash.
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


def sync_to_release(manager):
    """Encrypt rph.json and upload to GitHub Release.

    Returns True on success, False on failure so callers can propagate it.
    """
    tag = "rphjson"
    file_path = str(manager.file_manager.data_dir / "rph.json")
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
                create_result = subprocess.run(
                    ["gh", "release", "create", tag, "--repo", repo, "--title", title, "--notes", title],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if create_result.returncode != 0:
                    logger.error(f"Release create failed: {create_result.stderr.strip()}")
                    return False

            upload_result = subprocess.run(
                ["gh", "release", "upload", tag, archive_path, "--repo", repo, "--clobber"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if upload_result.returncode != 0:
                logger.error(f"Release upload failed: {upload_result.stderr.strip()}")
                return False
            edit_result = subprocess.run(
                ["gh", "release", "edit", tag, "--repo", repo, "--title", title, "--notes", title],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if edit_result.returncode != 0:
                logger.error(f"Release edit failed: {edit_result.stderr.strip()}")
                return False
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


def sync_to_email(manager):
    """Send update report via Resend email.

    Returns True on success, False on failure so callers can propagate it.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    recipient = os.environ.get("NOTIFICATION_EMAIL")
    if not api_key or not recipient:
        logger.warning("Missing RESEND_API_KEY or NOTIFICATION_EMAIL")
        return True

    data = getattr(manager, "_last_update_details", None)
    if not data:
        logger.info("No update details found — skipping email")
        return True

    new = data.get("new_details", [])
    mod = data.get("modified_details", [])
    rem = data.get("removed_details", [])

    if not new and not mod and not rem:
        logger.info("No changes — skipping email")
        return True

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
            safe_c = html_escape(c)
            html += f'<div style="margin-bottom:18px;"><div style="font-size:11px;font-weight:700;color:#111827;text-transform:uppercase;margin-bottom:6px;letter-spacing:1px;">{safe_c} ({len(recs)})</div>'  # noqa: E501
            for r in sorted(recs, key=reg_no):
                parts = r.split(" - ", 1)
                reg = html_escape(parts[0])
                name = html_escape(re.sub(r"\s*\(.*?\)$", "", parts[1] if len(parts) > 1 else r).strip())
                html += f'<div style="font-size:13px;color:#6b7280;padding:4px 0;"><span style="font-family:ui-monospace,monospace;">{reg}</span> - {name}</div>'  # noqa: E501
            html += "</div>"
        return html + "</div>"

    new_t, mod_t, rem_t = new, mod, rem

    text = f"TGPC RPh Index Sync Report\n{sync_time}\n\n"
    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
        '<body style="font-family:-apple-system,sans-serif;background:#ffffff;padding:15px 20px;color:#374151;line-height:1.3;margin:0;">'  # noqa: E501
        '<div style="max-width:600px;">'
        f'<h2 style="margin:0;font-size:17px;line-height:1.2;"><span style="color:#00cc66;">TGPC</span> <span style="color:#ef4444;">RPh</span> <span style="color:#9ca3af;">Index</span> Sync Report</h2>'  # noqa: E501
        f'<div style="color:#6b7280;font-size:12px;margin-bottom:30px;font-weight:500;">{sync_time}</div>'
        f"{fmt_html('🌱 NEW', new_t, '#00cc66')}{fmt_html('🌀 CHANGES', mod_t, '#2563eb')}{fmt_html('❌ REMOVALS', rem_t, '#ef4444')}"  # noqa: E501
        '<div style="margin-top:15px;font-size:11px;color:#9ca3af;padding-top:10px;">'
        '<div style="font-weight:700;"><span style="color:#00cc66;">TGPC</span> <span style="color:#ef4444;">RPh</span> <span style="color:#9ca3af;">Index</span></div>'  # noqa: E501
        "<div>Open-Source TGPC Pharmacist Data</div></div></div></body></html>"
    )
    for label, items, total in [("NEW", new_t, new), ("CHANGES", mod_t, mod), ("REMOVALS", rem_t, rem)]:
        if total:
            text += f"{label} ({len(total)}):\n" + "\n".join(sorted(items, key=lambda x: (cat(x), reg_no(x)))) + "\n\n"
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


def email_record_list(manager, reg_ids) -> bool:
    """Email a record list using the DEFAULT make-scrape report format.

    Standing rule: every records email uses sync_to_email() — no custom
    formats. Builds _last_update_details from the given registration IDs
    (as NEW) and delegates to sync_to_email().
    """
    reg_ids = sorted(set(reg_ids))
    lookup = {r.registration_number: r for r in manager.file_manager.load()}
    new_details = [f"{rid} - {lookup[rid].name} ({lookup[rid].category})" for rid in reg_ids if rid in lookup]
    if not new_details:
        logger.info("No matching records found in rph.json — skipping email")
        return True
    new_cat_stats = dict(sorted(Counter(lookup[r].category for r in reg_ids if r in lookup).items()))
    manager._last_update_details = {
        "new_details": new_details,
        "modified_details": [],
        "removed_details": [],
        "new_cat_stats": new_cat_stats,
        "rem_cat_stats": {},
        "mod_cat_stats": {},
        "total_records": len(lookup),
    }
    return sync_to_email(manager)
