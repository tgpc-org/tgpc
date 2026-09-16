"""
Quota checker for all services used by TGPC.
"""

import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from tgpc.utils import setup_logging

logger = setup_logging("tgpc.quota")

FREE_TIER = {
    "supabase": {
        "database_size_gb": 0.5,
        "storage_size_gb": 1.0,
        "egress_gb": 5.0,
        "mau": 50000,
        "edge_function_invocations": 500000,
    },
    "cloudflare_r2": {
        "storage_gb": 10.0,
        "class_a_ops": 1_000_000,
        "class_b_ops": 10_000_000,
    },
    "resend": {
        "emails_per_day": 100,
        "emails_per_month": 3000,
    },
    "google_drive": {
        "storage_gb": 15.0,
    },
}


def _get_supabase_project_ref():
    """Extract project ref from SUPABASE_URL."""
    url = os.environ.get("SUPABASE_URL", "")
    m = re.search(r"https://([^.]+)\.supabase\.co", url)
    return m.group(1) if m else None


def _supabase_pat():
    return os.environ.get("SUPABASE_PAT", "")


def _cf_token():
    return os.environ.get("CLOUDFLARE_API_TOKEN", "")


def _cf_account_id():
    return os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")


def _resend_key():
    return os.environ.get("RESEND_API_KEY", "")


def _req(url, headers, timeout=15):
    """Simple HTTP GET wrapper."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return {"status": resp.status, "body": body, "headers": dict(resp.headers)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode(), "headers": dict(e.headers)}
    except Exception as e:
        return {"status": 0, "body": str(e), "headers": {}}


def _req_json(url, headers, data, timeout=15):
    """Simple HTTP POST with JSON body."""
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return {"status": resp.status, "body": body, "headers": dict(resp.headers)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode(), "headers": dict(e.headers)}
    except Exception as e:
        return {"status": 0, "body": str(e), "headers": {}}


def _r2_bucket_name():
    return "tgpc"


# --- Supabase ---


def check_supabase():
    ref = _get_supabase_project_ref()
    pat = _supabase_pat()
    if not ref or not pat:
        return {"error": "Missing SUPABASE_PAT or SUPABASE_URL"}

    base = f"https://api.supabase.com/v1/projects/{ref}"
    headers = {"Authorization": f"Bearer {pat}"}

    result = {"database_size_gb": None, "storage_size_gb": None, "api_requests": None}

    # API requests count
    r = _req(f"{base}/analytics/endpoints/usage.api-requests-count", headers)
    if r["status"] == 200:
        result["api_requests"] = json.loads(r["body"]).get("count", 0)

    # Database size via Supabase Management API. The endpoint is
    # POST /v1/projects/{ref}/database/query (same as the /api/usage
    # endpoint) — the old `/sql` path never matched and always fell through.
    sql = "SELECT (sum(pg_database_size(datname)) / 1073741824.0)::numeric(10,4) as size_gb FROM pg_database"
    r = _req_json(
        f"{base}/database/query",
        headers,
        {"query": sql},
    )
    if r["status"] == 200:
        data = json.loads(r["body"])
        if data:
            result["database_size_gb"] = float(data[0]["size_gb"])

    # Storage size
    sql2 = (
        "SELECT (sum((metadata->>'size')::int) / (1024.0*1024.0*1024.0))::numeric(10,4) as size_gb FROM storage.objects"
    )
    r = _req_json(f"{base}/database/query", headers, {"query": sql2})
    if r["status"] == 200:
        data = json.loads(r["body"])
        if data and data[0]["size_gb"]:
            result["storage_size_gb"] = float(data[0]["size_gb"])
        else:
            result["storage_size_gb"] = 0.0

    return result


# --- Cloudflare R2 ---


def check_r2():
    token = _cf_token()
    account_id = _cf_account_id()
    if not token or not account_id:
        return {"error": "Missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID"}

    base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
    headers = {"Authorization": f"Bearer {token}"}

    result = {"storage_bytes": None, "object_count": None, "class_a_ops": None, "class_b_ops": None}

    # Bucket usage
    bucket = _r2_bucket_name()
    r = _req(f"{base}/r2/buckets/{bucket}/usage", headers)
    if r["status"] == 200:
        data = json.loads(r["body"]).get("result", {})
        result["storage_bytes"] = int(data.get("payloadSize", 0))
        result["object_count"] = int(data.get("objectCount", 0))

    # Class A/B operations via GraphQL
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    gql = {
        "query": """
        query {
            viewer {
                accounts(filter: {accountTag: "%s"}) {
                    r2OperationsAdaptiveGroups(
                        limit: 10000
                        filter: {
                            datetime_geq: "%s"
                            datetime_leq: "%s"
                        }
                    ) {
                        sum { requests }
                        dimensions { actionType }
                    }
                }
            }
        }
        """
        % (account_id, month_start.isoformat(), now.isoformat())
    }
    r = _req_json(f"{base}/graphql", headers, gql)
    if r["status"] == 200:
        data = json.loads(r["body"])
        actions = data.get("data", {}).get("viewer", {}).get("accounts", [{}])[0].get("r2OperationsAdaptiveGroups", [])
        class_a = 0
        class_b = 0
        class_a_types = {
            "PutObject",
            "DeleteObject",
            "ListObjects",
            "CreateMultipartUpload",
            "PutBucket",
            "DeleteBucket",
            "HeadBucket",
        }
        class_b_types = {"GetObject", "HeadObject"}
        for action in actions:
            action_type = action.get("dimensions", {}).get("actionType", "")
            count = action.get("sum", {}).get("requests", 0)
            if action_type in class_a_types:
                class_a += count
            elif action_type in class_b_types:
                class_b += count
        result["class_a_ops"] = class_a
        result["class_b_ops"] = class_b

    return result


# --- GitHub Actions ---

# --- Resend ---


def check_resend():
    key = _resend_key()
    if not key:
        return {"error": "Missing RESEND_API_KEY"}

    headers = {"Authorization": f"Bearer {key}"}
    result = {"daily_quota": None, "monthly_quota": None}

    r = _req("https://api.resend.com/emails?limit=1", headers)
    if r["status"] in (200, 422):
        h = r["headers"]
        daily = h.get("x-resend-daily-quota")
        monthly = h.get("x-resend-monthly-quota")
        if daily is not None:
            result["daily_quota"] = int(daily)
        if monthly is not None:
            result["monthly_quota"] = int(monthly)

    return result


# --- Google Drive ---


def check_google_drive():
    config_b64 = os.environ.get("RCLONE_GDRIVE_CONFIG")
    if not config_b64:
        return {"error": "Missing RCLONE_GDRIVE_CONFIG"}

    config_path = "/tmp/rclone-quota.conf"
    try:
        import base64

        Path(config_path).write_bytes(base64.b64decode(config_b64))
    except Exception:
        return {"error": "Invalid RCLONE_GDRIVE_CONFIG"}

    result = {"storage_used_gb": None, "storage_total_gb": None}

    try:
        r = subprocess.run(
            ["rclone", "about", "gdrive:", "--json", "--config", config_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            used = data.get("used", 0)
            total = data.get("total", 0)
            result["storage_used_gb"] = round(used / 1073741824, 4) if used else 0.0
            result["storage_total_gb"] = round(total / 1073741824, 2) if total else 15.0
    except Exception:
        pass
    finally:
        Path(config_path).unlink(missing_ok=True)

    return result


# --- Main ---


def _pct(used, limit):
    if limit is None or used is None:
        return "N/A"
    if limit == 0:
        return "-"
    return f"{used / limit * 100:.1f}%"


def _fmt_usage(used, limit, unit=""):
    if used is None:
        return f"N/A / {_fmt_val(limit)}{unit}"
    if limit is None:
        return f"{_fmt_val(used)}{unit} / ?"
    return f"{_fmt_val(used)}{unit} / {_fmt_val(limit)}{unit}"


def _fmt_val(val):
    if val is None:
        return "?"
    if isinstance(val, float) and val < 100:
        return f"{val:.2f}"
    if isinstance(val, float):
        return f"{val:,.1f}"
    return f"{val:,}"


def show_quotas():
    print("=" * 60)
    print("  TGPC Service Quota Report")
    print("=" * 60)
    print()

    # --- Supabase ---
    print("── Supabase ─────────────────────────────────────────────")
    sb = check_supabase()
    if "error" in sb:
        print(f"  ⚠  {sb['error']}")
    else:
        ft = FREE_TIER["supabase"]
        db = sb.get("database_size_gb")
        st = sb.get("storage_size_gb")
        ar = sb.get("api_requests")
        print(f"  Database:      {_fmt_usage(db, ft['database_size_gb'], 'GB')}    {_pct(db, ft['database_size_gb'])}")
        print(f"  Storage:       {_fmt_usage(st, ft['storage_size_gb'], 'GB')}    {_pct(st, ft['storage_size_gb'])}")
        print(f"  API Requests:  {_fmt_val(ar)} total")

    print()

    # --- Cloudflare R2 ---
    print("── Cloudflare R2 ─────────────────────────────────────────")
    r2 = check_r2()
    if "error" in r2:
        print(f"  ⚠  {r2['error']}")
    else:
        ft = FREE_TIER["cloudflare_r2"]
        sb = r2.get("storage_bytes")
        sb_gb = sb / 1073741824 if sb else 0
        oc = r2.get("object_count")
        ca = r2.get("class_a_ops")
        cb = r2.get("class_b_ops")
        print(f"  Storage:       {_fmt_usage(sb_gb, ft['storage_gb'], 'GB')}    {_pct(sb_gb, ft['storage_gb'])}")
        print(f"  Objects:       {_fmt_val(oc)}")
        print(f"  Class A Ops:   {_fmt_usage(ca, ft['class_a_ops'], '')}    {_pct(ca, ft['class_a_ops'])}")
        print(f"  Class B Ops:   {_fmt_usage(cb, ft['class_b_ops'], '')}    {_pct(cb, ft['class_b_ops'])}")

    print()

    # --- Resend ---
    print("── Resend (Email) ────────────────────────────────────────")
    resend_usage = check_resend()
    if "error" in resend_usage:
        print(f"  ⚠  {resend_usage['error']}")
    else:
        ft = FREE_TIER["resend"]
        dq = resend_usage.get("daily_quota")
        mq = resend_usage.get("monthly_quota")
        print(f"  Daily:         {_fmt_usage(dq, ft['emails_per_day'], '')}    {_pct(dq, ft['emails_per_day'])}")
        print(f"  Monthly:       {_fmt_usage(mq, ft['emails_per_month'], '')}    {_pct(mq, ft['emails_per_month'])}")

    print()

    # --- Google Drive ---
    print("── Google Drive ──────────────────────────────────────────")
    gd = check_google_drive()
    if "error" in gd:
        print(f"  ⚠  {gd['error']}")
    else:
        ft = FREE_TIER["google_drive"]
        su = gd.get("storage_used_gb")
        st = gd.get("storage_total_gb")
        print(f"  Storage:       {_fmt_usage(su, st or ft['storage_gb'], 'GB')}    {_pct(su, st or ft['storage_gb'])}")

    print()
    print("=" * 60)


if __name__ == "__main__":
    show_quotas()
