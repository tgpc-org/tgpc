# TGPC — Architecture & Developer Context

> **IMPORTANT:** Before making ANY code change, read this document fully and update it when done. This file is the single source of truth for context across sessions.

---

## Repository Overview

Code-only repository for the **Telangana Pharmacy Council (TGPC)** pharmacist registration search tool. Tracks source code but excludes data artifacts (e.g., `data/rph.json`, `data/update_details.json`, `data/webp/`, sweep JSONLs), credentials, and IDE files.

The project consists of:
- **Python pipeline** (`tgpc/`) — scrapes data from the Telangana Pharmacy Council website, syncs to Supabase, Cloudflare R2, Google Drive, GitHub Release, and sends email notification
- **Frontend** (`ui/`) — production website served via Cloudflare Pages (SvelteKit)
- **Documentation** (`ARCHITECTURE.md`, `CODE_REVIEW.md`, `README.md`)

---

## Directory Map

```
tgpc/
├── .github/workflows/rphsync.yml   # Manual CI: single job, scrapes + syncs to all destinations + email
├── .github/workflows/python.yml    # ruff + pytest + pip-audit dependency scan
├── .github/workflows/ui.yml        # eslint + svelte-check + brand-color gate + tests + npm audit
├── .husky/                         # Husky pre-commit hook → triggers pre-commit (ruff)
├── .pre-commit-config.yaml         # ruff lint + ruff-format only
├── pyproject.toml                  # Package: tgpc-data-extraction v2.0.0, min-version deps
├── .gitignore
├── ARCHITECTURE.md
├── README.md
├── data/
│   ├── rph.json                     # ~89K pharmacist records (JSON array) — gitignored but tracked historically
│   ├── update_details.json         # Sync diff summary — gitignored
│   ├── webp/                       # Per-record enrichment photos (WebP) — gitignored
│   ├── inactive_records.jsonl      # Inactive-sweep phase 1 output — gitignored
│   ├── now_active_from_inactive.jsonl # Inactive-sweep phase 2 output — gitignored
│   └── backups/                    # Timestamped rph.json backups (R2-backed) — gitignored
├── tgpc/                           # Python package
│   ├── __init__.py                 # Imports Config, setup_logging, Scraper, Manager; __version__ = "2.0.0"
│   ├── __main__.py                 # CLI: python3 -m tgpc {update, sync, enrich, retry-photos, quota, creds} + WARP mgmt
│   ├── utils.py                    # Config dataclass, TGPCError/BlockedError, setup_logging, credentials (Keychain/env/file)
│   ├── progress.py                 # ProgressBar, Phase, heartbeat, BarHandler (TTY + CI-safe output)
│   ├── quota.py                    # Free-tier quota report (Supabase, R2, Resend, GDrive)
│   ├── scraper.py                  # Scraper, RateLimiter, PharmacistRecord, extractors, TLS adapter
│   ├── manager.py                  # FileManager, BackupManager, Manager (~1550 lines)
│   ├── inactive_sweep.py           # Detect inactive→active reactivations (2-phase, resumable)
│   └── enrich_actives.py           # Parallel enrichment + upsert of reactivated records
├── ui/                            # Production frontend (SvelteKit)
│   ├── src/
│   │   ├── routes/
│   │   │   ├── +layout.svelte     # Shared header/footer/stats bar
│   │   │   ├── +page.svelte       # Search page
│   │   │   ├── notice/+page.svelte
│   │   │   ├── dispatch/+page.svelte
│   │   │   ├── rph/[registration_number]/  # SSR profile page with SEO
│   │   │   ├── admin/+page.svelte (server load gates payload)
│   │   │   └── api/
│   │   │       ├── admin/+server.ts      # POST login / DELETE logout (session cookie)
│   │   │       ├── usage/+server.ts      # Service quota report (fail-closed)
│   │   │       ├── dispatch/+server.ts   # R2 bucket listing proxy (stale fallback)
│   │   │       ├── dispatch/[name]/+server.ts # PDF proxy w/ title rewrite
│   │   │       ├── health/+server.ts     # Connectivity + staleness health check
│   │   │       └── notice/+server.ts     # Static notice JSON
│   │   └── lib/
│   │       ├── supabase.ts        # Supabase client (anon key)
│   │       ├── api.ts              # Search/record/stats API + input sanitization + ranking
│   │       ├── cache.ts            # localStorage TTL cache helpers
│   │       ├── colors.ts          # CATEGORY_COLORS (exempt from brand gate)
│   │       ├── r2.ts              # R2 public URLs from PUBLIC_R2_PHOTO_BASE
│   │       ├── types.ts            # Shared TS interfaces
│   │       ├── DatePicker.svelte
│   │       ├── components/         # Clock.svelte, ProfileSidebar.svelte
│   │       └── server/             # Compiler-enforced server-only: auth.ts, adminLinks.ts, rateLimit.ts, auth.test.ts
│   ├── static/
│   │   ├── favicon.svg, .ico, -192.png
│   │   ├── pdf.svg, notice.json, manifest.json
│   ├── wrangler.toml              # R2 DISPATCH bucket binding
│   └── svelte.config.js
├── tests/                          # 62 tests, 7 files (all mocked — no real HTTP/Supabase)
│   ├── test_scraper.py             # 10: timeouts, WAF/blocked detection, table fallback, bad rows, detail parsing, legacy headers, missing tables
│   ├── test_manager_update.py      # 7: safety guard, dedup/sort/GITHUB_OUTPUT, deterministic ordering, source-unavailable, +3 sync return-value regressions
│   ├── test_manager_enrichment.py  # 3: enrichment save, registration mismatch, null serial_number regression
│   ├── test_manager_sync.py        # 17: every sync destination's fail-closed/success/failure contract
│   ├── test_manager_photos.py      # 11: photo upload/verify/retry pipeline, batch error isolation
│   ├── test_quota.py               # 8: quota reporter helpers + fail-closed paths
│   └── test_inactive_sweep.py      # 6: JSONL parsing, checkpoint roundtrip, resume/partial runs
└── (credentials stored in macOS Keychain, not files)
```

---

## Python Backend

### Dependency Graph

```
__main__.py ─── Manager ─── Config (utils.py)
                     ├── FileManager (load/save rph.json as JSON array)
                     ├── BackupManager (timestamped backups, cleanup after 30 days)
                     ├── Scraper (scraper.py)
                     │       ├── RateLimiter (adaptive delay 3-8s)
                     │       ├── PharmacistRecord dataclass
                     │       ├── extract_basic_records() — table parser via BeautifulSoup
                     │       └── extract_detailed_info() — per-record detail page parser
                     └── Sync methods:
                           ├── sync_to_supabase() — upsert to rph table, update metadata.last_sync
                           ├── sync_to_supabase_storage() — upload rph.json to Supabase Storage
                           ├── sync_to_r2() — aws s3api put-object to Cloudflare R2
                           ├── sync_to_gdrive() — rclone copyto Google Drive
                           ├── sync_to_release() — gh release upload rph.json
                           └── sync_to_email() — Resend API email with change details
```

### Entry Point: `tgpc/__main__.py`

```bash
python3 -m tgpc update              # Restore-if-missing → health check → backup → scrape → dedup → safety guard → save → sync to all destinations + email → enrich new records
python3 -m tgpc update --no-sync    # Scrape only, skip cloud sync
python3 -m tgpc update --force      # Override the 100-churn/1000-new safety caps
python3 -m tgpc sync                # Sync to all destinations
python3 -m tgpc enrich              # Enrich records (photo, gender, status, validity) for pending records
python3 -m tgpc retry-photos        # Retry uploading failed photos from data/webp/ to R2
python3 -m tgpc quota               # Show free quota usage for all services
python3 -m tgpc creds {set,list,delete}  # Manage credentials in macOS Keychain
python3 -m tgpc.inactive_sweep {inactive,sweep}  # Detect inactive→active reactivations (resumable)
python3 -m tgpc.enrich_actives      # Parallel enrichment of reactivated records
```

For `update`/`sync`/`enrich`/`retry-photos`, the CLI connects Cloudflare WARP for network routing (auto-disconnects via `atexit`) when `warp-cli` is available.

`load_credentials()` loads env vars first, then macOS Keychain (via `security`), then `~/.config/tgpc/creds.sh` / `tgpc-creds.sh` file fallback.

### `tgpc/utils.py` — Config & Exceptions

```python
class TGPCError(Exception):
    def __init__(self, message: str, original_error: Optional[Exception] = None)

class BlockedError(TGPCError):
    # Raised when the source serves a block/WAF page (200 + markers) —
    # recoverable as "source unavailable", distinct from parser bugs

@dataclass
class Config:
    base_url: str = "https://www.pharmacycouncil.telangana.gov.in"
    connect_timeout: int = 20
    read_timeout: int = 180
    max_retries: int = 3
    proxy_url: Optional[str] = None        # from TGPC_PROXY_URL, HTTPS_PROXY, or HTTP_PROXY
    min_delay: float = 3.0                 # RateLimiter floor
    max_delay: float = 8.0                 # RateLimiter ceiling
    long_break_after: int = 100            # Requests before long break (not actively used in RateLimiter)
    long_break_duration: int = 60
    data_directory: str = "data"
    enrichment_directory: str = "data"     # Override via TGPC_ENRICHMENT_DIR
    user_agent: str = "Mozilla/5.0 ..."
```

Config is loaded via `Config.load()` classmethod (reads env vars for proxy and enrichment dir). It does NOT contain Supabase credentials — those are read from env vars at sync time.

### `tgpc/scraper.py` — Data Extraction

**PharmacistRecord** dataclass:
- Core fields: `registration_number`, `name`, `father_name`, `category`, `serial_number`
- Optional detail fields: `gender`, `validity_date`, `status`, `education` (list of dicts), `work_experience` (dict)
- `to_dict()` → strict 5-field dict (for `rph.json`)
- `to_detailed_dict()` → 10-field dict (for enrichment JSON files)

**RateLimiter:**
- Adaptive delay: starts at `min_delay` (3s), adjusts based on success/failure
- On success: `current_delay *= 0.9` (faster)
- On failure: `current_delay *= 1.5` (slower, capped at `max_delay` 8s)
- Jitter: `delay * random.uniform(0.8, 1.2)`

**Scraper:**
- Uses `requests.Session` with connection pooling (10 pools, 10 max)
- Two API endpoints (constructed from `config.base_url`):
  - `total`: `{base_url}/pharmacy/srchpharmacisttotal` — full listing table
  - `search`: `{base_url}/pharmacy/getsearchpharmacist` — detail search (POST with `registration_no`)
- `health_check(timeout=30)` → `bool` — hits total endpoint, checks for blocked indicators
- `_request(method, url)` → `requests.Response` — wrapped with `@tenacity.retry` (3 attempts, exponential backoff 2-10s), calls `rate_limiter.wait()` before each request, runs block detection (status, access denied, captcha, short response)
- `extract_basic_records()` → `List[PharmacistRecord]` — fetches total endpoint, finds `<table id="tablesorter-demo">` (fallback to any `<table>`), extracts rows with ≥5 cells (serial, reg_no, name, father, category)
- `extract_detailed_info(reg_no, img_dir)` → `Optional[PharmacistRecord]` — POSTs to search endpoint, parses detail page for: registration table (name, father, gender, category, status, validity), education table (qualification → category, university, college, years, HT No), work experience table (address, state, district, pin code), and photos (base64 data URI or URL download → saved to `img_dir`)

### `tgpc/manager.py` — Orchestration (~1550 lines)

**`DataIntegrityError`** — raised when enrichment scraped data doesn't match the expected registration.

**`FileManager`** — handles local JSON storage:
- `save(records, filename)` → JSON array with `indent=2, ensure_ascii=False`
- `load(filename)` → deserializes `PharmacistRecord` list

**`BackupManager`** — timestamped backups (format: `rph_backup_YYYYMMDD_HHMMSS.json`), cleanup deletes files older than 30 days.

**`Manager.run_daily_update()`** — the core update workflow:
1. **Health check** → if blocked, writes `update_status=blocked` to GITHUB_OUTPUT, returns `"blocked"`
2. **Backup** existing `rph.json`
3. **Scrape** fresh data via `scraper.extract_basic_records()`
4. **Source unavailable check** — if scrape raises exception matching `_is_source_unavailable_error()` (timeouts, connection errors, 429/5xx), writes `source_unavailable`, preserves existing data, returns `"source_unavailable"`
5. **Empty check** — if no records returned, returns `"empty_scrape"`
6. **Safety guard** — if fresh count < 90% of existing, aborts with `"safety_abort"`
7. **Deduplicate** by registration number, sort by serial_number
8. **Calculate diffs** — new, removed, modified records with per-category stats
9. **Save** new data to `rph.json`, cleanup old backups
10. **Write `update_details.json`** with change details
11. **Write GITHUB_OUTPUT** for CI consumption
12. Returns `"updated"`

**`Manager.sync_to_supabase()`**:
- Reads `SUPABASE_URL`, `SUPABASE_SECRET_KEY` from env
- Creates Supabase client, batch upserts to `rph` table (1000/batch, `on_conflict="registration_number"`)
- Updates `metadata.last_sync` timestamp

**`Manager.sync_to_supabase_storage()`**:
- POSTs `rph.json` to `{url}/storage/v1/object/tgpc/rph.json` with `x-upsert: true`

**`Manager.sync_to_r2()`**:
- Reads `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
- Runs `aws s3api put-object --endpoint-url https://{account_id}.r2.cloudflarestorage.com --bucket tgpc --key rph.json`

**`Manager.sync_to_gdrive()`**:
- Reads `RCLONE_GDRIVE_CONFIG` (base64-encoded rclone config file)
- Writes to temp file, runs `rclone copyto rph.json gdrive:tgpc/rph.json`
- Cleans up temp config

**`Manager.sync_to_release()`**:
- Builds an AES-256 encrypted zip of `rph.json` in-process via `pyzipper` (password from `RELEASE_PASSWORD`, never on a process argv) and uploads it to GitHub Release tag `rphjson` via `gh`
- Creates release if it doesn't exist, updates title with record count
- Skips (returns True) when `RELEASE_PASSWORD` is unset

**`Manager.sync_to_email()`**:
- Reads `RESEND_API_KEY`, `NOTIFICATION_EMAIL` from env
- Reads `_last_update_details` (set by `run_daily_update()`)
- Builds HTML + plain text email with categorized change details (new/changed/removed by category)
- Sends via Resend API using `requests` (POST to `https://api.resend.com/emails`)
- Capped at 200 items per section in email

**`Manager.run_enrichment(start, stop)`**:
- Health check → queries Supabase `rph` table for records missing enrichment fields
- Load `rph.json`, filter pending records sorted by serial
- Optionally restrict to `start`/`stop` serial range
- Calls `_process_records_sequential()` → for each record: scrapes detail page, validates registration/name/father/category match (raises `DataIntegrityError` on mismatch), converts photo to WebP in `data/webp/`, uploads to R2 (`photos/{reg}.webp`) with size verification, deletes the local copy, then upserts all 10 fields directly to Supabase

**`Manager.enrich_new_records(force)`** — auto-enriches records newly discovered by the last update (skips already-enriched via a Supabase check; aborts above 1000 records without `--force`).
**`Manager.retry_photos()`** — retries R2 uploads for files left in `data/webp/` from a failed session.

### `tgpc/inactive_sweep.py` + `tgpc/enrich_actives.py` — reactivation pipeline
- `inactive` (phase 1): pull `status='Inactive'` rows from Supabase, join with `rph.json` identity fields → `data/inactive_records.jsonl`
- `sweep` (phase 2): re-scrape each record in batches of 1000, appending now-`Active` ones to `data/now_active_from_inactive.jsonl`; checkpoint file makes the run resumable; `--workers N` parallelizes with per-worker Scrapers
- `enrich_actives.py`: parallel enrichment + Supabase upsert of the reactivated records (same integrity guard and photo contract as `_process_records_sequential`)

### `tgpc/progress.py` — output plumbing
- `ProgressBar` — animated single-line bar on a TTY (background spinner thread, survives `sleep`/`subprocess`); discrete lines every N updates + heartbeats otherwise (CI-friendly)
- `Phase` — `[N/M] label` headers with `— done`/`— FAILED` footers; `heartbeat()` — indeterminate progress for single-shot operations
- `step()` — granular sub-steps routed to the active bar; `BarHandler` — logging to stderr that clears/redraws the bar so log lines never corrupt it

### `tgpc/quota.py` — quota report
- `python3 -m tgpc quota` prints free-tier usage for Supabase (DB size, storage, API requests), Cloudflare R2 (storage, objects, Class A/B ops via GraphQL), Resend (daily/monthly quota headers), and Google Drive (`rclone about`)
- Each `check_*` fails closed with a `Missing … credentials` message rather than raising

### Dependencies (`pyproject.toml`)

```toml
dependencies = [
    "requests>=2.32.3",
    "beautifulsoup4>=4.12.3",
    "tenacity>=8.2.3",        # @retry decorator in scraper._request
    "supabase>=2.28.0",       # create_client for sync_to_supabase
    "Pillow>=12.3.0",         # EXIF/alpha flattening + WebP conversion in enrichment
    "pyzipper>=0.4.0",        # AES-256 release archive in sync_to_release
]
```

Deps are floor-pinned (min versions, not exact). CVE patching is handled by manual bumps (e.g. commit 51ad6c6) plus the `pip-audit` / `npm audit --audit-level=high` gates in CI.

### Supabase Schema

```sql
CREATE TABLE rph (
  registration_number TEXT PRIMARY KEY,
  name TEXT,
  father_name TEXT,
  category TEXT,
  serial_number TEXT,
  -- enrichment columns (written by the enrichment pipeline):
  gender TEXT,
  validity_date TEXT,          -- 'DD-Mon-YYYY' as scraped
  status TEXT,                 -- 'Active' | 'Inactive' | …
  education JSONB,             -- [{Category, Board/University, College Name, College Address, From, To, HT No}]
  work_experience JSONB,       -- {Address, State, District, Pin code}
  photo_url TEXT               -- R2 public URL, only set after a verified upload
);

CREATE TABLE metadata (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- RPC functions (created via Supabase dashboard):
-- get_rph_stats() → { total: int, active: int, inactive: int, categories: { BPharm: int, DPharm: int, MPharm: int, PharmD: int, QC: int, QP: int } }
-- search_pharmacists(q text, lim int) → ranked rows via ts_rank + similarity
```

RLS allows anonymous `SELECT` on `rph` and `metadata` tables. The publishable/anon key (set as `PUBLIC_SUPABASE_PUBLISHABLE_KEY` in the Cloudflare Pages dashboard / local `ui/.env`, which is gitignored) is safe to expose.

**⚠️ RLS verification (do this in the Supabase dashboard → SQL Editor):**

The frontend uses the **publishable/anon key** against 87k rows, so protection from
unauthorized writes/deletes depends entirely on RLS being enabled. Run this to
confirm the right posture (public **read-only**, no writes from anon):

```sql
-- Confirm RLS is enabled and anon can only SELECT
select schemaname, tablename, rowsecurity
from pg_tables
where schemaname = 'public' and tablename in ('rph', 'metadata');

-- Anon should have a SELECT (read) policy and NO insert/update/delete policy.
-- If the query below returns any rows for rph/metadata, tighten it:
select polname, polcmd, pg_get_expr(polqual, polrelid)
from pg_policy
join pg_class on pg_class.oid = polrelid
join pg_namespace on pg_namespace.oid = relnamespace
where nspname = 'public' and relname in ('rph', 'metadata');

-- Correct anon posture — read-only. Run these if the SELECT policy is missing:
alter table public.rph enable row level security;
alter table public.metadata enable row level security;

create policy "anon select rph" on public.rph
  for select to anon using (true);

create policy "anon select metadata" on public.metadata
  for select to anon using (true);
```

The service-role key (used server-side / by the Python pipeline) bypasses RLS, so
writes continue to work via `sync_to_supabase()`.

**✅ Verified state (2026-09-01):** RLS is enabled on both tables
(`rowsecurity = true` on `rph` and `metadata`). Six policies are in place —
the anon/public/`realtime` roles have **read-only** (`SELECT`) policies only,
and the service role has full access. This is the correct read-only posture for
a public search portal; the publishable/anon key is safe to expose.

### Data File Formats

**`rph.json`** — standard JSON array:
```json
[
  {"registration_number": "TG12345", "name": "...", "father_name": "...", "category": "BPharm", "serial_number": 12345},
  ...
]
```

**`update_details.json`** — written by `run_daily_update()`:
```json
{
  "new_details": ["TG12345 - Name (BPharm)", ...],
  "modified_details": ["TG12346 - Other Name (DPharm)", ...],
  "removed_details": [],
  "new_cat_stats": {"BPharm": 5, "DPharm": 2},
  "rem_cat_stats": {},
  "mod_cat_stats": {"BPharm": 1},
  "total_records": 87500
}
```

**Sweep JSONLs** — `data/inactive_records.jsonl` (phase 1: one identity-fields object per line) and `data/now_active_from_inactive.jsonl` (phase 2: now-Active records with gender/validity/status). `data/inactive_sweep_checkpoint.json` records completed batch indices for resumability.

### Environment Variables

| Variable | Used By | Purpose |
|---|---|---|
| `SUPABASE_URL` | `sync_to_supabase()`, quota, CI | Supabase project URL |
| `SUPABASE_SECRET_KEY` | `sync_to_supabase()`, CI | Service role key (NOT the anon key) |
| `SUPABASE_PAT` | `quota` | Supabase account-level PAT for the quota report (also the `/api/usage` endpoint) |
| `CLOUDFLARE_ACCOUNT_ID` | `sync_to_r2()`, quota, CI | R2 endpoint account ID |
| `CLOUDFLARE_API_TOKEN` | `quota` | Cloudflare API token for R2 usage queries (quota only — not R2 data sync) |
| `R2_ACCESS_KEY_ID` | `sync_to_r2()`, CI | R2 S3-compatible access key |
| `R2_SECRET_ACCESS_KEY` | `sync_to_r2()`, CI | R2 S3-compatible secret key |
| `RCLONE_GDRIVE_CONFIG` | `sync_to_gdrive()`, CI | Base64-encoded rclone Google Drive config |
| `RESEND_API_KEY` | `sync_to_email()`, CI | Resend.com API key |
| `NOTIFICATION_EMAIL` | `sync_to_email()`, CI | Email recipient for sync report |
| `RELEASE_PASSWORD` | `sync_to_release()`, CI | Password for the AES-256 encrypted release zip |
| `TGPC_PROXY_URL` | `Config.load()` | Optional outbound proxy for scraping |
| `TGPC_ENRICHMENT_DIR` | `Config.load()` | Override enrichment working directory |
| `TGPC_R2_PUBLIC_BASE` | `Config.load()` | R2 public bucket base URL (default: the `pub-…r2.dev` host) |

---

## Frontend (SvelteKit — Production)

Built with SvelteKit 5 + Tailwind CSS v4 + TypeScript.

### Stack

| Layer | Technology |
|---|---|
| Framework | SvelteKit 5 (`@sveltejs/adapter-cloudflare`) |
| Styling | Tailwind CSS v4 |
| Database | Supabase (anon key with RLS — `SELECT` only) |
| Hosting | Cloudflare Pages (build: `ui/.svelte-kit/cloudflare`) |
| Env vars | Public: `PUBLIC_SUPABASE_URL`, `PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `PUBLIC_R2_PHOTO_BASE`. Server-side (Pages dashboard): `ADMIN_SECRET` (preferred) or `QUOTA_SECRET` for the admin session; `ADMIN_LINK_PHARMACIST_URL` / `ADMIN_LINK_EMAIL_VERIFY_URL` for the two credential-bearing admin links (no token fallbacks exist in code) |

### Routes

| Path | Description |
|---|---|
| `/` | Search: table (desktop) / cards (mobile), filter chips, result refiners (RPC/name/father/gender/status/valid-till), PDF/CSV export |
| `/notice` | Notices table with year tabs, search, link badges |
| `/dispatch` | Dispatch PDF grid with year tabs, search |
| `/rph/[registration_number]` | SSR pharmacist profile page (SEO title/description/OG image, education + work experience sections) |
| `/admin` | Operator console (usage report + internal TGPC links); payload served server-side only to a valid session |
| `/api/admin` | POST login (rate-limited, constant-time compare) issues an HttpOnly signed-cookie session; DELETE logs out |
| `/api/usage` | Service quota report; fails closed without `ADMIN_SECRET`/`QUOTA_SECRET`; accepts session cookie or `x-quota-secret` header |
| `/api/dispatch` | JSON — lists PDFs from R2 bucket (`dispatch/` prefix); stale-flagged fallback list when the binding is unavailable |
| `/api/dispatch/[name]` | Streams a dispatch PDF from R2 (strict `DL…pdf` name validation) and rewrites its `/Title` metadata so the tab shows the filename |
| `/api/health` | Connectivity + staleness health check (Supabase latency, last_sync freshness; 503 when down) |
| `/api/notice` | JSON — notice data from `static/notice.json` |

Server-only modules under `ui/src/lib/server/` (`auth.ts`, `adminLinks.ts`, `rateLimit.ts`) are compiler-enforced: SvelteKit fails the build if client code imports them.

### Key Features

- **Stats bar** — 7 category cards with live counts from Supabase RPC, cached in localStorage
- **Realtime** — Supabase Realtime subscription on `metadata` table for live stats/timestamp updates
- **Search** — client-side Supabase query (min 3 chars, debounced 300ms) via `search_pharmacists` RPC (no row cap — all matches returned), ranked by prefix priority then numeric; falls back to a sanitized PostgREST `.or()` query if the RPC fails. Result refiners (RPC/name/father/gender/status/valid-till) filter client-side; results render in a single scrollable list sized to the viewport (`content-visibility: auto` on rows)
- **Export** — PDF via jsPDF + jspdf-autotable; CSV via Blob download with formula-injection guard
- **Security headers** — applied globally in `hooks.server.ts` (+ `ui/static/_headers` for static assets):
  - **CSP** with a per-request **nonce** for inline scripts on route HTML (`script-src 'self' 'nonce-<n>' 'strict-dynamic'`) plus `img-src`/`connect-src` allowlists for the R2 photo CDN and Supabase origin
  - `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Strict-Transport-Security`, `Permissions-Policy`
  - `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Resource-Policy: same-origin` (cross-origin isolation)
- **Admin auth** — HMAC-SHA256 signed, expiring session cookie (`HttpOnly`/`SameSite=Strict`); login uses constant-time comparison + a fixed 250ms delay on every attempt (timing side-channel + brute-force throttling); best-effort in-isolate rate limiter (defence-in-depth — pair with a Cloudflare Rate Limiting rule on `/api/admin`)
- **Responsive** — single component, CSS toggles between table and cards at 768px
- **Connection status** — status pill (Busy/Live/Offline) with live clock
- **Design** — TGPC brand palette only, machine-enforced by `npm run check:colors`

---

## CI/CD

### GitHub Actions: `.github/workflows/rphsync.yml`

**Trigger:** `workflow_dispatch` (manual). Input: `force_sync` (boolean, default false).

**Single job `rphsync`** with these steps:

1. **Checkout** repository
2. **Install Python deps** (`pip install -e .` + `supabase` + `awscli`)
3. **Setup Cloudflare WARP** — install, register, connect (outbound routing for scraping/sync)
4. **Create data directory** (`mkdir -p data/backups`)
5. **Restore artifact** — `gh run download` artifact `rph-data` if `data/rph.json` doesn't exist locally
6. **Run data update** — `python3 -m tgpc update`; when `force_sync` is true and local data exists, runs `python3 -m tgpc sync` instead. All cloud syncs happen inside the CLI (delta to Supabase; Storage/R2/GDrive/Release/email only when there are changes); any destination failure exits non-zero and fails the job
7. **Upload data artifact** — `rph-data` with 90-day retention (`if: always()`)
8. **Notify on failure** — sends a failure-report email via Resend when `RESEND_API_KEY`/`NOTIFICATION_EMAIL` are configured

Job permissions: `actions: write`, `contents: write` (release upload).

**Secrets:**
| Secret | Purpose |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SECRET_KEY` | Supabase service role key |
| `R2_ACCESS_KEY_ID` | R2 S3-compatible access key |
| `R2_SECRET_ACCESS_KEY` | R2 S3-compatible secret key |
| `CLOUDFLARE_ACCOUNT_ID` | R2 endpoint account ID |
| `RCLONE_GDRIVE_CONFIG` | Base64-encoded rclone GDrive config |
| `RESEND_API_KEY` | Resend email API key |
| `NOTIFICATION_EMAIL` | Email recipient |
| `RELEASE_PASSWORD` | Password for the encrypted release zip |

**Quality gates:**
- `.github/workflows/ui.yml` runs on push/PR touching `ui/` — ESLint + brand-color gate (`check:colors`) + svelte-check + the 18 unit tests + a build with placeholder PUBLIC env vars (real values live in the Cloudflare Pages dashboard) + `npm audit --audit-level=high`. Auto-deploys from `main` build `ui/`.
- `.github/workflows/python.yml` runs on push/PR touching `tgpc/`, `tests/`, or `pyproject.toml` — `ruff check`, `ruff format --check` (pinned 0.16.6, matching pre-commit), the full pytest suite, and a `pip-audit` dependency vulnerability scan.

**Dependency updates:**
Dependabot was removed (2026-09) in favour of manual bumps. CVE coverage comes from the two audit gates: `pip-audit` in `python.yml` and `npm audit --audit-level=high` in `ui.yml` — both fail the build on known-vulnerable dependencies.

---

## Testing

```bash
python3 -m pytest tests/ -v
```

62 tests across 7 files:

| File | Tests | What's tested |
|---|---|---|
| `test_scraper.py` | 10 | `_request` timeouts + clean pass-through, WAF/blocked detection (`BlockedError`), `extract_basic_records` (no table, bad rows, fallback table), `extract_detailed_info` (no records, full parse with photo/education/validity, legacy headers, missing tables) |
| `test_manager_update.py` | 7 | Safety guard (90% threshold), dedup/sort/GITHUB_OUTPUT, deterministic detail ordering, source-unavailable skip, +3 regressions covering `sync_to_*` return values |
| `test_manager_enrichment.py` | 3 | Enrichment saves first pending record, raises DataIntegrityError on registration mismatch, resolves records with `serial_number = None` (M2 regression) |
| `test_manager_sync.py` | 17 | Every `sync_to_*` destination's contract: fail-closed on missing credentials, True on success, False on transport/API failure. Release test uses real pyzipper and verifies encryption at upload time; email test asserts the Resend request shape |
| `test_manager_photos.py` | 11 | Photo upload→verify→local-delete pipeline, retry/backoff, size-mismatch rejection, batch error isolation (scrape/upsert failures don't abort the batch), `retry_photos` |
| `test_quota.py` | 8 | Quota reporter helpers (ref parsing, formatting) and every `check_*` fail-closed path on missing credentials |
| `test_inactive_sweep.py` | 6 | JSONL parsing (good/bad lines), checkpoint save/load roundtrip, resume skipping completed batches, partial-run slicing |

All tests use mocking (no real HTTP or Supabase calls). The `supabase` module is mocked globally before imports.

### Frontend

**Unit tests:** `ui/test:unit` runs `node --experimental-strip-types --test 'src/**/*.test.ts'` — 18 tests covering signed-cookie session creation/verification, constant-time comparison, and the `isAuthed` fail-closed path (no secret). No test framework beyond Node's built-in runner.

---

## Pre-commit

`.pre-commit-config.yaml` runs on commit for staged files:

**Python** (root, via `astral-sh/ruff-pre-commit` v0.16.6): `ruff check --fix` + `ruff-format` on changed Python files.

**UI** (local hooks, run from `ui/`): `ui-eslint` (ESLint flat config, 0 errors), `ui-svelte-check` (svelte-check), and `ui-check-colors` (brand-color gate, `npm run check:colors`) on changed Svelte/TS/JS files. `requirements`: a `node`/`npm` install is expected on the dev machine.

No Black, no trailing-whitespace, or end-of-file-fixer hooks.

---

## Deployment

| Component | Method | URL |
|---|---|---|---|
| Frontend | Cloudflare Pages (auto-deploy from `main`, builds `ui/`) | `https://tgpc.pages.dev` |
| CI/CD | GitHub Actions (manual trigger) | `github.com/tgpc-org/tgpc/actions` |
| Data download | GitHub Release | Tag `rphjson`, file `rph.json` |

---

## See Also

- `ui/src/lib/BrandColors.md` — mandatory palette and enforcement notes
- `README.md` — Project overview and quick-start
