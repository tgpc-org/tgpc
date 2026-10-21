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
├── .github/FUNDING.yml             # GitHub Sponsors + PayPal funding config
├── .husky/                         # Husky pre-commit hook → triggers pre-commit (ruff)
├── .pre-commit-config.yaml         # ruff lint + ruff-format only
├── pyproject.toml                  # Package: tgpc-data-extraction v2.0.0, min-version deps (requires-python >=3.10)
├── uv.lock                         # Locked dependency graph (uv lock) — reproducible installs
├── supabase/migrations/            # Version-controlled schema, RLS, RPC + hardening SQL
├── docs/security/                  # Security audit findings (JSON, with resolution log)
├── Makefile                        # scrape / sync / quota + DG run-control targets (dg-start/stop/resume/watch)
├── .gitignore
├── ARCHITECTURE.md
├── README.md
├── CLAUDE.md                       # Agent quick context (commands, sync destinations, palette)
├── AGENTS.md                       # Agent working rules (brand palette, workflow, email format)
├── CODE_REVIEW.md
├── DG_PIPELINE.md                  # DG contact-pipeline deep dive (captcha capture, fetch-dg CLI, dashboard :8765)
├── VPS_DG_FETCH.md                 # Runbook: DG fetch on Oracle Always Free VPS (bootstrap → smoke → systemd)
├── data/
│   ├── rph.json                     # ~91K pharmacist records (JSON array) — gitignored
│   ├── update_details.json         # Sync diff summary — gitignored
│   ├── webp/                       # Per-record enrichment photos (WebP) — gitignored
│   ├── inactive_records.jsonl      # Inactive-sweep phase 1 output — gitignored
│   ├── now_active_from_inactive.jsonl # Inactive-sweep phase 2 output — gitignored
│   ├── dg_contacts.jsonl           # DG contact capture (PII) — gitignored
│   ├── dg_fetch_checkpoint.json    # DG fetch resume state — gitignored
│   ├── dg_raw/                     # Per-record DG raw snapshots — gitignored
│   └── backups/                    # Timestamped rph.json backups (R2-backed) — gitignored
├── tgpc/                           # Python package
│   ├── __init__.py                 # Imports Config, setup_logging, Scraper, Manager; __version__ = "2.0.0"
│   ├── __main__.py                 # CLI: python3 -m tgpc {update, sync, enrich, retry-photos, fetch-dg, validate-dg, quota, creds} + WARP mgmt
│   ├── utils.py                    # Config dataclass, TGPCError/BlockedError, setup_logging, credentials (Keychain/env/file)
│   ├── progress.py                 # ProgressBar, Phase, heartbeat, BarHandler (TTY + CI-safe output)
│   ├── quota.py                    # Free-tier quota report (Supabase, R2, Resend, GDrive)
│   ├── scraper.py                  # Scraper, RateLimiter, PharmacistRecord, extractors, TLS adapter
│   ├── manager.py                  # Manager facade (~730 lines): restore, update orchestration, photo pipeline
│   ├── storage.py                  # R2Client, FileManager, BackupManager, validate_rph_backup, DataIntegrityError
│   ├── sync.py                     # Sync destination bodies: Supabase DB/Storage, R2, GDrive, Release, Email
│   ├── enrichment.py               # run_enrichment / enrich_new_records / process_records_sequential bodies
│   ├── inactive_sweep.py           # Detect inactive→active reactivations (2-phase, resumable)
│   ├── enrich_actives.py           # Parallel enrichment + upsert of reactivated records
│   ├── details_dg.py               # DG getdetailsdg captcha flow → PII contacts; L1-L4 redundancy + --resume (~1390 lines)
│   ├── merge_refresh.py            # Post-scrape recompute of DG-merged rph columns (idempotent, warn-and-continue)
│   ├── backfill_missing.py         # Blank-only column fills from the live source (strictly additive)
│   ├── dg_migration.sql            # rph_dg_contacts table DDL (service-role only, no anon grants)
│   └── dg_serial_int_migration.sql # rph_dg_contacts.serial_number TEXT -> INTEGER
├── scripts/                        # Standalone helpers (run from repo root)
│   ├── dg_dashboard.py             # Local DG fetch dashboard (localhost, no login: status + START/STOP + VPS controls)
│   ├── dg_dashboard.html           # DG monitor UI (dark mode, start/stop, live stats)
│   ├── dg_captcha_bench.py         # Fetch N live DG captchas, OCR-guess, dump for human labeling
│   ├── vps_bootstrap.sh            # One-time cloud VM setup (apt, WARP, clone, rph.json, ~/.tgpc_env)
│   ├── vps_fetch.sh                # VPS DG fetch loop (batches, cloud sync, R2 checkpoint backup + ctl polling; --smoke for trial)
│   ├── dg_run.sh                   # One-command recurring launcher (preflight → drift guards → push → smoke-gated start; set DG_VM)
│   ├── vps_ctl.sh                  # No-SSH VPS ops over R2 (status/halt/resume/exclude via ops/ctl.json)
│   ├── vm_view.sh                  # Re-open the Mac→VM dashboard tunnel (:8900; sleep kills it)
│   ├── tgpc-dg-fetch.service       # systemd unit for the VPS fetch loop
│   ├── tgpc-dg-dashboard.service   # systemd unit for the VPS dashboard (localhost-only)
│   ├── vm_sweep_inactives.sh       # VM-side re-sweep of inactive records (runs tgpc.inactive_sweep sweep)
│   ├── dg_open.sh                  # Start the local DG dashboard and open it in the browser
│   └── optimize_images.py          # Batch data/img/ → WebP q85, passport 413x531 resize
├── ui/                            # Production frontend (SvelteKit)
│   ├── src/
│   │   ├── app.html               # HTML shell (%sveltekit.head/body); inline theme bootstrap
│   │   ├── app.css                # Global stylesheet (Tailwind v4 entry, theme tokens)
│   │   ├── app.d.ts               # Ambient types
│   │   ├── env.ts                 # defineEnvVars: every server-read var must be declared here
│   │   ├── hooks.server.ts        # Security headers on every function response (CSP lives in vite.config.ts)
│   │   ├── routes/
│   │   │   ├── +layout.svelte     # Shared header/footer/stats bar
│   │   │   ├── +layout.ts         # SSR stats/last-sync load (via /api/stats)
│   │   │   ├── +page.svelte       # Search page
│   │   │   ├── notice/+page.svelte
│   │   │   ├── dispatch/+page.svelte
│   │   │   ├── rph/[registration_number]/  # SSR profile page with SEO
│   │   │   ├── admin/+page.svelte (server load gates payload)
│   │   │   ├── sandbox/           # LOCAL-ONLY dev loader (gitignored data, never ships)
│   │   │   ├── sitemap.xml/+server.ts
│   │   │   └── api/
│   │   │       ├── admin/+server.ts      # POST login / DELETE logout (session cookie)
│   │   │       ├── admin/contacts/+server.ts # GET ?reg= session-gated DG contact lookup (service-side join, PII never public)
│   │   │       ├── usage/+server.ts      # Service quota report (fail-closed)
│   │   │       ├── dispatch/+server.ts   # R2 bucket listing proxy (stale fallback)
│   │   │       ├── dispatch/[name]/+server.ts # PDF proxy w/ title rewrite
│   │   │       ├── search/+server.ts     # Public ranked search proxy (capped 1..200)
│   │   │       ├── record/+server.ts     # Public single-record proxy
│   │   │       ├── refine/+server.ts      # Public refined-search proxy (allowlisted enums)
│   │   │       ├── stats/+server.ts      # Public stats + last-sync proxy
│   │   │       ├── health/+server.ts     # Connectivity + staleness health check
│   │   │       └── notice/+server.ts     # Static notice JSON
│   │   └── lib/
│   │       ├── api.ts              # Search/record/stats API + input sanitization + ranking
│   │       ├── cache.ts            # localStorage TTL cache helpers
│   │       ├── colors.ts          # TGPC palette + CATEGORY_COLORS (exempt from brand gate)
│   │       ├── dates.ts            # Shared MONTHS table + DD-Mon-YYYY parse/format helpers
│   │       ├── exporters.ts        # PDF (jsPDF) + CSV export with formula-injection guard (+ exporters.test.ts)
│   │       ├── fitToViewport.ts    # availableHeight() + `use:fitToViewport` action: list sizing above the fixed footer (+ fitToViewport.test.ts)
│   │       ├── motion.ts           # prefersReducedMotion() helper
│   │       ├── r2.ts              # R2 public URLs from PUBLIC_R2_PHOTO_BASE
│   │       ├── recent.ts           # Recently-viewed records (localStorage) (+ recent.test.ts)
│   │       ├── searchLimits.ts     # MAX_SEARCH_RESULTS cap + isTruncated() hint (+ searchLimits.test.ts)
│   │       ├── searchUrl.ts        # URL <-> search-state serialization (+ searchUrl.test.ts)
│   │       ├── theme.ts            # Light/dark theme store (localStorage tgpc-theme, system-preference fallback)
│   │       ├── types.ts            # Shared TS interfaces
│   │       ├── DatePicker.svelte
│   │       ├── components/         # Clock, ProfileSidebar, ContactDetails, MobileCards, Refiners, ResultsTable, SearchForm
│   │       └── server/             # Compiler-enforced server-only: appEnv.ts, auth.ts, adminLinks.ts, contacts.ts, gate.ts, rateLimit.ts (+ their .test.ts)
│   ├── static/
│   │   ├── favicon.svg, .ico, -192.png
│   │   ├── pdf.svg, notice.json, manifest.json
│   │   ├── .well-known/strix-verify.txt
│   │   └── _headers               # Security headers for Pages-served static assets (no CSP here — nonce comes from hooks)
│   ├── playwright.config.ts       # Playwright: desktop + mobile (iPhone-SE) projects; baseURL = PROD_URL ?? tgpc.pages.dev
│   ├── wrangler.toml              # R2 DISPATCH bucket binding
│   ├── vite.config.ts             # SvelteKit plugin + adapter-cloudflare + the single CSP definition
│   ├── scripts/check-colors.mjs   # Brand-color gate (0 offenders required)
│   ├── e2e/                       # Playwright specs (chromium; CI `e2e` job)
│   │   ├── smoke.spec.ts          # Homepage shell, search flow, notice/dispatch, API locking
│   │   ├── a11y.spec.ts           # axe: zero serious/critical violations (contrast excluded — see contrast spec)
│   │   ├── contrast.spec.ts       # No NEW color-contrast violations vs baseline
│   │   ├── mobile.spec.ts         # iPhone-SE viewport: no overflow, usable search, footer layering
│   │   ├── drawer.spec.ts         # Admin console drawer
│   │   ├── gate.spec.ts           # Unauthenticated contract: 302 -> /admin, 403 on gated APIs
│   │   ├── searchUrl.spec.ts      # URL <-> search-state roundtrip
│   │   ├── auth.setup.ts          # One-time admin login (E2E_ADMIN_SECRET) -> e2e/.auth.json
│   │   ├── contrast-baseline.json # Tracked contrast debt baseline
│   │   └── update-baseline.mjs    # Refresh the baseline after intentional palette changes
├── tests/                          # 195 tests, 13 files (all mocked — no real HTTP/Supabase)
│   ├── test_scraper.py             # 24: timeouts, WAF/blocked detection, table fallback, bad rows, detail parsing, legacy headers, missing tables, TLS verification + source-host rules
│   ├── test_manager_update.py      # 7: safety guard, dedup/sort/GITHUB_OUTPUT, deterministic ordering, source-unavailable, +3 sync return-value regressions
│   ├── test_manager_enrichment.py  # 3: enrichment save, registration mismatch, null serial_number regression
│   ├── test_manager_sync.py        # 17: every sync destination's fail-closed/success/failure contract
│   ├── test_manager_photos.py      # 11: photo upload/verify/retry pipeline, batch error isolation
│   ├── test_quota.py               # 8: quota reporter helpers + fail-closed paths
│   ├── test_inactive_sweep.py      # 6: JSONL parsing, checkpoint roundtrip, resume/partial runs
│   ├── test_bugfix_regressions.py  # 18: restore/backup/release/force regressions
│   ├── test_security_audit_regressions.py  # 27: audit remediations (DG PII RLS posture, R2 publicity gate, email escaping, rclone temp paths)
│   ├── test_details_dg.py          # 47: DG caption/detail parsing, captcha handling, RLS/Storage posture
│   ├── test_dg_dashboard.py        # 19: DG monitor status merge, zombie-PID detection, IST formatting
│   ├── test_merge_refresh.py       # 3: post-scrape merged-column refresh (fill/newer-wins/skip rules)
│   └── test_backfill_missing.py    # 5: blank-only backfill contract (never overwrites, never blanks)
└── (credentials stored in macOS Keychain, not files)
```

---

## Python Backend

### Dependency Graph

```
__main__.py ─── Manager ─── Config (utils.py)
                     ├── FileManager (tgpc/storage.py — load/save rph.json as JSON array)
                     ├── BackupManager (tgpc/storage.py — timestamped backups, cleanup after 30 days)
                     ├── Scraper (scraper.py)
                     │       ├── RateLimiter (adaptive delay 3-8s)
                     │       ├── PharmacistRecord dataclass
                     │       ├── extract_basic_records() — table parser via BeautifulSoup
                     │       └── extract_detailed_info() — per-record detail page parser
                     ├── tgpc/sync.py — Sync destinations (Manager methods delegate; bodies live here):
                     │       ├── sync_to_supabase() — upsert to rph table, update metadata.last_sync
                     │       ├── sync_to_supabase_storage() — upload rph.json to Supabase Storage
                     │       ├── sync_to_r2() — boto3/R2Client put-object to Cloudflare R2
                     │       ├── sync_to_gdrive() — rclone copyto Google Drive
                     │       ├── sync_to_release() — gh release upload rph.json
                     │       └── sync_to_email() — Resend API email with change details
                     └── tgpc/enrichment.py — Enrichment bodies:
                             ├── run_enrichment() — serial-range enrichment
                             ├── enrich_new_records() — auto-enrich records from the last update
                             └── process_records_sequential() — scrape → validate → photo → upsert
```

### Entry Point: `tgpc/__main__.py`

```bash
python3 -m tgpc update              # Restore-if-missing → health check → backup → scrape → dedup → safety guard → save → sync to all destinations + email → enrich new records → refresh merged columns
python3 -m tgpc update --no-sync    # Scrape only, skip cloud sync
python3 -m tgpc update --force      # Override the 100-churn/1000-new safety caps
python3 -m tgpc sync                # Sync to all destinations
python3 -m tgpc enrich              # Enrich records (photo, gender, status, validity) for pending records
python3 -m tgpc retry-photos        # Retry uploading failed photos from data/webp/ to R2
python3 -m tgpc quota               # Show free quota usage for all services
python3 -m tgpc fetch-dg --ids-file data/dg_ids.txt --captcha auto --sync-cloud  # DG contact capture (see DG_PIPELINE.md)
python3 -m tgpc validate-dg         # Offline validation pass over data/dg_contacts.jsonl
python3 -m tgpc creds {set,list,delete}  # Manage credentials in macOS Keychain
python3 -m tgpc.inactive_sweep {inactive,sweep}  # Detect inactive→active reactivations (resumable)
python3 -m tgpc.enrich_actives      # Parallel enrichment of reactivated records
```

For `update`/`sync`/`enrich`/`retry-photos`/`fetch-dg`, the CLI connects Cloudflare WARP for network routing (auto-disconnects via `atexit`) when `warp-cli` is available.

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
    base_url: str = "https://pharmacycouncil.telangana.gov.in"  # apex, not www. (C4: valid cert)
    connect_timeout: int = 20
    read_timeout: int = 180
    max_retries: int = 3
    proxy_url: Optional[str] = None        # from TGPC_PROXY_URL, HTTPS_PROXY, or HTTP_PROXY
    tls_cert_sha256: Optional[str] = None  # from TGPC_TLS_CERT_SHA256 — opt-in TGPC cert pin
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
- **TLS** — full verification everywhere: certificate **chain** *and* **hostname**. The source's certificate covers the apex name `pharmacycouncil.telangana.gov.in` but **not** `www.`, so `Config.base_url` points at the apex (override with `TGPC_BASE_URL`); both hosts serve the same endpoints — the bulk listing and detail pages are byte-identical once the per-session `jsessionid` is normalised, and the source returns the same response for an apex URL carrying a `www.` `Referer` — and the apex one is what makes strict hostname checking possible (CODE_REVIEW.md C4, resolved 2026-10). `TGPC_TLS_CERT_SHA256` optionally pins the exact peer certificate **in addition to** the hostname match — defence in depth against a compromised CA (audit F5/F10). Every other host (photo CDNs, R2) is verified by the default adapter.
- Applies `Config.proxy_url` (TGPC_PROXY_URL / HTTPS_PROXY / HTTP_PROXY) to the session; sends the single `Config.user_agent` header
- Two API endpoints (constructed from `config.base_url`):
  - `total`: `{base_url}/pharmacy/srchpharmacisttotal` — full listing table
  - `search`: `{base_url}/pharmacy/getsearchpharmacist` — detail search (POST with `registration_no`)
- `health_check(timeout=30)` → `bool` — hits total endpoint, checks for blocked indicators
- `_request(method, url)` → `requests.Response` — wrapped with `@tenacity.retry` (3 attempts, exponential backoff 2-10s), calls `rate_limiter.wait()` before each request, runs block detection (status, access denied, captcha, short response)
- `extract_basic_records()` → `List[PharmacistRecord]` — fetches total endpoint, finds `<table id="tablesorter-demo">` (fallback to any `<table>`), extracts rows with ≥5 cells (serial, reg_no, name, father, category)
- `extract_detailed_info(reg_no, img_dir)` → `Optional[PharmacistRecord]` — POSTs to search endpoint, parses detail page for: registration table (name, father, gender, category, status, validity), education table (qualification → category, university, college, years, HT No), work experience table (address, state, district, pin code), and photos (base64 data URI or URL download → saved to `img_dir`)
- **Photo SSRF guard** — a photo `src` may only point at a TGPC host (`_is_tgpc_host`: apex or `www.`), the scheme must be http(s), and no path may contain `..`. Redirects are followed manually (max 4 hops) with the **same checks re-applied at every hop** — `allow_redirects=True` would let a 302 move the fetch to any host after the initial same-host check.

### `tgpc/manager.py` — Orchestration facade (~730 lines)

The former 1,600-line god class was split into focused modules; `Manager`
delegates to them and re-exports their names so `from tgpc.manager import
Manager, R2Client, ...` and the `tgpc.manager.*` test patch targets keep
working.

- `tgpc/storage.py` — `R2Client` (boto3 wrapper), `FileManager`,
  `BackupManager`, `validate_rph_backup()`, `DataIntegrityError`
- `tgpc/sync.py` — bodies for every `sync_to_*` destination, `email_record_list()`,
  `delete_supabase_orphans()`; receive the `Manager` instance as first arg and
  build clients through `manager._make_supabase_client()` / `_make_r2_client()`
  factories so `tgpc.manager.create_client` / `tgpc.manager.R2Client` patches apply
- `tgpc/enrichment.py` — `run_enrichment()`, `enrich_new_records()`,
  `process_records_sequential()` on the same contract

Kept in `manager.py`: `__init__` (load_credentials → Config.load → managers →
Scraper), the client factories, update orchestration
(`run_daily_update`, `_restore_rph_from_backup`, `_write_update_outputs`,
source-unavailable classification), and the photo pipeline
(`upload_and_verify_photo`, `_upload_photo_to_r2`, `_verify_photo_on_r2`,
`retry_photos` — tests patch these `Manager` methods directly).

**`DataIntegrityError`** — raised when enrichment scraped data doesn't match the expected registration.

**`FileManager`** — handles local JSON storage:
- `save(records, filename)` → JSON array with `indent=2, ensure_ascii=False`
- `load(filename)` → deserializes `PharmacistRecord` list

**`BackupManager`** — timestamped backups (format: `rph_backup_YYYYMMDD_HHMMSS.json`), cleanup deletes files older than 30 days.

**Restore** (`Manager._restore_rph_from_backup`) — when the local `rph.json` is missing or below the minimum record count, backup candidates are tried **newest first**, each downloaded to a temp file. A candidate is only used once `validate_rph_backup` accepts it: a JSON list, at or above the record floor, with every row an object carrying a non-empty `registration_number`. So a corrupt newest upload falls through to an older good backup rather than aborting the whole update, and the local registry is never replaced by an unvalidated payload.

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
- Uploads `rph.json` via `R2Client` (boto3 `put_object` against `https://{account_id}.r2.cloudflarestorage.com`, bucket `tgpc`) — no `aws` CLI subprocess

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
- Sends all new/changed/removed records (no item cap)

**`Manager.run_enrichment(start, stop)`**:
- Health check → queries Supabase `rph` table for records missing enrichment fields
- Load `rph.json`, filter pending records sorted by serial
- Optionally restrict to `start`/`stop` serial range
- Calls `_process_records_sequential()` → for each record: scrapes detail page, validates registration/name/father/category match (raises `DataIntegrityError` on mismatch), converts photo to WebP in `data/webp/`, uploads to R2 (`photos/{reg}.webp`) with size verification, deletes the local copy, then upserts all 10 fields directly to Supabase

**`Manager.enrich_new_records(force)`** — auto-enriches records newly discovered by the last update (skips already-enriched via a Supabase check; aborts above 1000 records without `--force`). A standalone `python3 -m tgpc enrich` (fresh process, no `_last_new_regs`) checks all of `rph.json` under the same 1000-record cap.
**`Manager.retry_photos()`** — retries R2 uploads for files left in `data/webp/` from a failed session.

### `tgpc/inactive_sweep.py` + `tgpc/enrich_actives.py` — reactivation pipeline
- `inactive` (phase 1): pull `status='Inactive'` rows from Supabase, join with `rph.json` identity fields → `data/inactive_records.jsonl`
- `sweep` (phase 2): re-scrape each record in batches of 1000, appending now-`Active` ones to `data/now_active_from_inactive.jsonl`; checkpoint file makes the run resumable; `--workers N` parallelizes with per-worker Scrapers
- `enrich_actives.py`: parallel enrichment + Supabase upsert of the reactivated records (same integrity guard and photo contract as `_process_records_sequential`)

### `tgpc/progress.py` — output plumbing
- `ProgressBar` — animated single-line bar on a TTY (background spinner thread, survives `sleep`/`subprocess`); discrete lines every N updates + heartbeats otherwise (CI-friendly)
- `Phase` — `[N/M] label` headers with `— done`/`— FAILED` footers; `heartbeat()` — indeterminate progress for single-shot operations
- `step()` — granular sub-steps routed to the active bar; `BarHandler` — logging to stderr that clears/redraws the bar so log lines never corrupt it

### `tgpc/merge_refresh.py` — post-scrape merged-column refresh
- Runs as Phase 4/4 of `python3 -m tgpc update`. Recomputes three dashboard
  backfills on `public.rph` from the live tables: `date_of_registration`,
  `home_state`, and `validity_date` (newer-wins, DD-MM-YYYY → DD-Mon-YYYY).
- Idempotent by construction: it recomputes from scratch every run and only
  writes rows that still need it, so a typical run writes zero rows.
- Writes with individual `UPDATE`s, never an upsert — an upsert would INSERT
  a row without a name and hit the `name` not-null constraint (`3d6483f`).
- Deliberately warn-and-continue: a refresh hiccup must never fail a scrape.
  Missing credentials fail closed with an `error` stat instead of raising.

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
    "boto3>=1.28.0",         # R2Client S3 wrapper (sync + photo upload)
    "Pillow>=12.3.0",         # EXIF/alpha flattening + WebP conversion in enrichment
    "pytesseract>=0.3.13",    # DG captcha OCR (solve_captcha)
    "pyzipper>=0.4.0",        # AES-256 release archive in sync_to_release
]
```

Deps are floor-pinned (min versions, not exact) in `pyproject.toml`, and
`uv.lock` pins the full resolved graph (51 packages) so CI and a fresh clone
install exactly the same versions. Regenerate with `uv lock`; install with
`uv sync`. `requires-python` is `>=3.10` (3.9 is EOL and Pillow requires 3.10).
CVE patching is handled by manual bumps (e.g. commit 51ad6c6) plus the
`pip-audit` / `npm audit --audit-level=high` gates in CI.

### Supabase Schema

```sql
CREATE TABLE rph (
  registration_number TEXT PRIMARY KEY,
  name TEXT,
  father_name TEXT,
  category TEXT,
  serial_number INTEGER,
  -- enrichment columns (written by the enrichment pipeline):
  gender TEXT,
  validity_date TEXT,          -- 'DD-Mon-YYYY' as scraped
  status TEXT,                 -- 'Active' | 'Inactive' | …
  education JSONB,             -- [{Category, Board/University, College Name, College Address, From, To, HT No}]
  work_experience JSONB,       -- {Address, State, District, Pin code}
  photo_url TEXT,              -- R2 public URL, only set after a verified upload
  -- merged columns (written by tgpc/merge_refresh.py from rph_dg_contacts):
  date_of_registration TEXT,   -- verbatim DD-MM-YYYY
  renewal_validity TEXT,       -- verbatim DD-MM-YYYY (own value wins over the DG table)
  home_state TEXT              -- uppercased state
);

-- DG contact details live in a SEPARATE table (never new columns on rph,
-- so DG writes cannot touch base rows; migration: tgpc/dg_migration.sql):
-- CREATE TABLE rph_dg_contacts (
--   registration_number TEXT PRIMARY KEY,  -- 1:1 with rph, no FK (DG must not block rph deletes)
--   serial_number INTEGER,                 -- tracker from rph (human-friendly ordering; migrated from TEXT 2026-10, see tgpc/dg_serial_int_migration.sql)
--   dob TEXT, date_of_registration TEXT, renewal_validity TEXT,  -- verbatim DD-MM-YYYY
--   home_address TEXT, home_state TEXT,
--   work_study_address TEXT, work_study_state TEXT,
--   mobile_no TEXT, email_id TEXT, dg_fetched_at TIMESTAMPTZ
-- );  -- service_role only: captcha-gated PII, deliberately NOT anon-readable

CREATE TABLE metadata (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- RPC functions (created via Supabase dashboard):
-- get_rph_stats() → { total: int, active: int, inactive: int, categories: { BPharm: int, DPharm: int, MPharm: int, PharmD: int, QC: int, QP: int } }
-- search_pharmacists(q text, lim int) → ranked rows via ts_rank + similarity
```

RLS is enabled on `rph` and `metadata` with read-only (`SELECT`) policies. Note
(re-verified 2026-10-21): the `anon` role holds **no SELECT grant** on `rph` at
all, so the policy is inert — the publishable key cannot read the table. That is
the stronger posture, not the documented one, and it matters because `rph`
carries DG PII columns (`dob`, `mobile_no`, `email_id`, `home_address`). The
schema, policies and RPC functions now live in version control under
`supabase/migrations/`; see that README for the drift that was found.

> **Note (2026-10):** the browser no longer holds the anon key. The "Layer 2" refactor moved every data read behind a rate-limited server proxy (`/api/search`, `/api/refine`, `/api/record`, `/api/stats`) that queries with the **service** key server-side. `ui/src/lib/supabase.ts` was deleted and `PUBLIC_SUPABASE_PUBLISHABLE_KEY` is no longer read by the app. The anon `SELECT` policies below are retained as defence in depth — if the key is ever reintroduced, they still permit read-only access and nothing more.

`rph_dg_contacts` is the deliberate exception. It holds captcha-gated PII (mobile, email, home address) that TGPC gates behind a captcha on `getdetailsdg`, so it carries no anon grant at all — the publishable key cannot read it, and the enrichment pipeline is its only consumer (service role). See `tgpc/dg_migration.sql`.

**⚠️ RLS verification (do this in the Supabase dashboard → SQL Editor):**

The anon key is not used by the deployed app, but the tables remain publicly
readable by it, so protection from unauthorized writes/deletes still depends
entirely on RLS being enabled. Run this to confirm the right posture (public
**read-only**, no writes from anon):

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

`rph_dg_contacts` needs the **opposite** check — captcha-gated PII must be
readable by nobody but the service role:

```sql
-- Expect rowsecurity = true …
select tablename, rowsecurity from pg_tables
where schemaname = 'public' and tablename = 'rph_dg_contacts';

-- …and zero policies. A leftover `anon select … USING (true)` is the flaw.
select polname, pg_get_expr(polqual, polrelid) from pg_policy
join pg_class on pg_class.oid = polrelid
join pg_namespace on pg_namespace.oid = relnamespace
where nspname = 'public' and relname = 'rph_dg_contacts';

-- …and no privilege for the roles a browser or signed-in user gets.
-- Expect `permission denied for table rph_dg_contacts`, not a row.
set role anon;
select mobile_no from public.rph_dg_contacts limit 1;
reset role;
```

**✅ Verified state (2026-10-21, supersedes 2026-09-01):** RLS is enabled on
`rph`, `metadata` and `rph_dg_contacts`. The deployed app never uses the anon
key (Layer 2), so the policies are defence in depth rather than the active path.
Re-querying the live database corrected two earlier assumptions:

- **`anon` has no SELECT grant on `rph`** (0 column privileges) — the table is
  not readable with the publishable key, despite the permissive SELECT policy.
- **`rph` carries PII columns** (`dob`, `mobile_no`, `email_id`, `home_address`;
  ~36k non-empty each). Together with the permissive policy that is one
  `GRANT SELECT` away from a leak, and `anon`/`authenticated` additionally held
  INSERT/UPDATE/DELETE/TRUNCATE grants (RLS blocks row writes; TRUNCATE is not
  RLS-protected). `supabase/migrations/20260101000003_security_hardening.sql`
  revokes them; it is written but **not yet applied**.
- The duplicate policies (five on `metadata`, two on `rph`) are normalised in
  `supabase/migrations/20260101000001_rls.sql`.

`rph_dg_contacts` is intentionally outside that count: it has RLS enabled with
**no** policies and no anon grant, because the registry's contact columns are
captcha-gated at the source.

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
| `TGPC_BASE_URL` | `Config.load()` | Override the source base URL. Default is the **apex** host `https://pharmacycouncil.telangana.gov.in` — its certificate covers that name, which is what allows strict TLS hostname verification (the `www.` variant's cert does not). |
| `TGPC_TLS_CERT_SHA256` | `Config.load()` | Optional SHA-256 fingerprint pin for the TGPC host certificate (hex, colons allowed). Unset = current behaviour (hostname match relaxed, chain validated); set = fingerprint enforced in place of the hostname check (audit F5/F10) |
| `TGPC_ENRICHMENT_DIR` | `Config.load()` | Override enrichment working directory |
| `TGPC_R2_PUBLIC_BASE` | `Config.load()` | R2 public bucket base URL (default: the `pub-…r2.dev` host) |
| `TGPC_R2_DG_BUCKET` | `details_dg.push_file_to_r2()` | **Private** bucket for DG PII artifacts. Unset (or set to the public bucket) = pushes refuse; never falls back to the public bucket |
| `TGPC_DG_WORKERS` | `fetch-dg` | DG fetch pool size (default 4, max 16) |
| `TGPC_DG_RETRY_TERMINAL` | `scripts/dg_run.sh` | Whether the VM run re-attempts terminal DG failures |

### Frontend env vars

The SvelteKit 3 app resolves server-side variables through `$app/env` (declared
in `ui/src/env.ts`); `envVal()` in `ui/src/lib/server/appEnv.ts` tries
`platform.env` first, then `$app/env`, and returns `null` when a variable is
unset so every consumer fails closed. `platform.env` alone is empty on
adapter-cloudflare v8 — reading it directly is the bug fixed in `8ee00ca`.

| Variable | Scope | Purpose |
|---|---|---|
| `PUBLIC_SUPABASE_URL` | public | Supabase project URL (health check) |
| `PUBLIC_R2_PHOTO_BASE` | public | R2 public photo/notice/dispatch base URL |
| `SUPABASE_URL` / `SUPABASE_SECRET_KEY` | private | Service-role access for the data proxies |
| `SUPABASE_PAT` | private | Account PAT for `/api/usage` and the admin contacts join |
| `ADMIN_SECRET` (preferred) / `QUOTA_SECRET` | private | Signs the admin session cookie; absent = nobody is authorized |
| `ADMIN_LINK_PHARMACIST_URL` / `ADMIN_LINK_EMAIL_VERIFY_URL` | private | The two credential-bearing admin links (no token fallbacks exist in code) |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | private | R2 usage queries for the quota report |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | private | R2 S3-compatible credentials |
| `TGPC_R2_DG_BUCKET` | private | Private DG bucket name (the app must never push PII to the public bucket) |

`PUBLIC_SUPABASE_PUBLISHABLE_KEY` is no longer read by the app — the browser
holds no database key at all.

---

## Frontend (SvelteKit — Production)

Built with SvelteKit 3 + Tailwind CSS v4 + TypeScript.

### Stack

| Layer | Technology |
|---|---|
| Framework | SvelteKit 3 (`@sveltejs/adapter-cloudflare`) |
| Styling | Tailwind CSS v4 |
| Database | Supabase via server-side proxies (service key, RLS read-only for anon) |
| Hosting | Cloudflare Pages (build: `ui/.svelte-kit/cloudflare`) |
| Env vars | Public: `PUBLIC_SUPABASE_URL`, `PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `PUBLIC_R2_PHOTO_BASE`. Server-side (Pages dashboard): `ADMIN_SECRET` (preferred) or `QUOTA_SECRET` for the admin session; `ADMIN_LINK_PHARMACIST_URL` / `ADMIN_LINK_EMAIL_VERIFY_URL` for the two credential-bearing admin links (no token fallbacks exist in code) |

### Routes

| Path | Description |
|---|---|
| `/` | Search: table (desktop) / cards (mobile), filter chips, result refiners (RPC/name/father/gender/status/valid-till), PDF/CSV export |
| `/notice` | Notices table with year tabs, search, link badges |
| `/dispatch` | Dispatch PDF grid with year tabs, search |
| `/rph/[registration_number]` | SSR pharmacist profile page (SEO title/description/OG image, education + work experience sections) |
| `/admin` | Operator console (USAGE + INTERNAL LINKS + CONTACTS tabs); payload served server-side only to a valid session |
| `/sandbox` | LOCAL-ONLY dev loader for contact-block layouts against real local datasets; gitignored, never ships |
| `/sitemap.xml` | Generated sitemap of profile pages |
| `/api/admin` | POST login (rate-limited, constant-time compare) issues an HttpOnly signed-cookie session; DELETE logs out |
| `/api/admin/contacts` | GET `?reg=` — session-gated DG contact lookup; PII never public |
| `/api/usage` | Service quota report; fails closed without `ADMIN_SECRET`/`QUOTA_SECRET`; accepts session cookie or `x-quota-secret` header |
| `/api/dispatch` | JSON — lists PDFs from R2 bucket (`dispatch/` prefix); stale-flagged fallback list when the binding is unavailable |
| `/api/dispatch/[name]` | Streams a dispatch PDF from R2 (strict `DL…pdf` name validation). Bytes are served **untouched** — rewriting `/Title` in place changes the file length without rebuilding the PDF xref table and corrupts downloads for strict readers; `Content-Disposition: inline; filename=…` gives the tab a sane title instead |
| `/api/search` | Public ranked search proxy → `search_pharmacists` RPC; `lim` clamped to 1..200 server-side; rate-limited per IP |
| `/api/refine` | Public refined-search proxy; allowlisted enums + sanitized text, one capped PostgREST query; rate-limited per IP |
| `/api/record` | Public single-record proxy (eleven safe columns); rate-limited per IP |
| `/api/stats` | Public stats + last-sync payload (service key server-side); polled every 60s, not realtime |
| `/api/health` | Connectivity + staleness health check (Supabase latency, last_sync freshness; 503 when down) |
| `/api/notice` | JSON — notice data from `static/notice.json` |

Server-only modules under `ui/src/lib/server/` (`appEnv.ts`, `auth.ts`, `adminLinks.ts`, `contacts.ts`, `gate.ts`, `rateLimit.ts`) are compiler-enforced: SvelteKit fails the build if client code imports them.

### Data access (Layer 2)

The browser holds **no database key**. Every data read goes through a
rate-limited server proxy that queries Supabase with the **service** key
server-side, so the anon/publishable key is never shipped to the client:

| Browser call | Proxy | Upstream |
|---|---|---|
| `searchRecords()` | `/api/search?q=&lim=` | `search_pharmacists` RPC |
| `searchWithRefiners()` | `/api/refine?...` | one capped PostgREST query |
| `getRecord()` | `/api/record?reg=` | one row, eleven allowlisted columns |
| `getStats()` / `getLastSync()` | `/api/stats` | stats RPC + `metadata.last_sync` |

Each proxy re-validates its input server-side (the client's `validateQuery()`
is a UX nicety, not the security boundary) and clamps the row ceiling to a
fixed literal, so no caller — browser or direct — can exceed it.

### Key Features

- **Shell** — sticky header (brand lockup · live status pill · theme toggle), a single horizontally scrollable stats strip (total + 6 category tiles + last-sync/active/inactive meta), and a primary tab nav with a measured green slider. Footer is in-flow on phones and fixed on `sm+`, so lists are sized to the footer by the shared `fitToViewport` action instead of hardcoded `100vh - Npx` offsets
- **Stats strip** — total + per-category counts from the Supabase stats RPC, fetched server-side via `/api/stats`, cached in localStorage
- **Freshness** — stats + last-sync polled every 60s from `/api/stats` instead of a Supabase Realtime channel (the channel needed the anon database key the browser no longer holds)
- **Search** — server-side search through `/api/search` (min 3 chars, debounced 300ms) → `search_pharmacists` RPC (capped at `MAX_SEARCH_RESULTS` — 200 rows per query), ranked by prefix priority then numeric. Result refiners (RPC/name/father/gender/status/valid-till) go through `/api/refine`, which re-validates every filter server-side and returns one capped, allowlisted result set; results render in a single scrollable list sized by the `fitToViewport` action (`content-visibility: auto` on rows), with sortable headers (RPC / name / category / valid-till / status, `aria-sort`-tracked) that fall back to the server's relevance rank. When a result set comes back full, the header says so and asks for a narrower query — `isTruncated()` in `ui/src/lib/searchLimits.ts` drives that hint, and `searchLimits.test.ts` fails if any query path drops its limit or asks for more than the cap
- **Shareable state** — the search query, refiners and sort order are mirrored to the URL (`searchUrl.ts`), so a link, a refresh and back/forward all restore the same result set

The 200-row ceiling is enforced in **two** places: the client cap in `searchLimits.ts` and a server-side clamp in each proxy (`lim` → 1..200 in `/api/search`, a fixed `HARD_LIMIT` in `/api/refine`). The server clamp is the real boundary — a caller invoking the endpoint directly cannot exceed it. Keep the RPC's own `lim := least(greatest(lim, 1), 200)` clamp and Supabase's API **Max Rows** setting as the backstop. The RPC clamp is version-controlled in `supabase/migrations/20260101000002_rpc.sql` (applied to production 2026-10-21); Supabase's API **Max Rows** setting remains a backstop and lives in the dashboard.
- **Export** — PDF via jsPDF + jspdf-autotable; CSV via Blob download with formula-injection guard
- **Security headers** — applied globally in `hooks.server.ts` (+ `ui/_headers` for static assets, project root as adapter-cloudflare v8 requires):
  - **CSP** with a per-request **nonce** for inline scripts on route HTML (`script-src 'self' 'nonce-<n>' 'strict-dynamic'`) plus `img-src`/`connect-src` allowlists for the R2 photo CDN and Supabase origin. The policy lives **only** in `vite.config.ts` (`csp.mode: 'auto'`) — SvelteKit injects the nonce and sends the matching header itself. Setting it again in `hooks.server.ts` or `_headers` creates a second enforced policy whose nonce never matches, which bricks hydration.
  - `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Strict-Transport-Security`, `Permissions-Policy`
  - `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Resource-Policy: same-origin` (cross-origin isolation)
- **Public portal, gated admin** — path classification lives in `#lib/server/gate.ts` (unit-tested) and is applied in `hooks.server.ts`. Search, profiles, notices, dispatch, stats and health are open; only `/admin`, `/api/admin/*`, `/api/usage` and `/sandbox` need a session. Gated API paths answer **403**, gated pages **302 → /admin**. The data APIs rate-limit per IP inside their own handlers instead of requiring a login.
- **Admin auth** — HMAC-SHA256 signed, expiring session cookie (`HttpOnly`/`SameSite=Strict`); login uses constant-time comparison + a fixed 250ms delay on every attempt (timing side-channel + brute-force throttling). Rate limiting is **two-tier** (`#lib/server/rateLimit.ts`): a per-isolate window that costs no I/O, plus an optional shared Workers KV counter (`RATE_LIMIT` binding) consulted *only* after a key exhausts its local budget — so ordinary traffic never touches KV, which matters because KV quota-caps writes and `/api/stats` is polled per open tab. KV errors fall back to the local verdict rather than failing the request. The local map is capped at 5,000 keys. The edge half is **not attachable to this deployment**: the account owns no zones and `tgpc.pages.dev` is Cloudflare-owned, so a Cloudflare Rate Limiting rule needs a custom domain on an owned zone first.
- **Admin contacts lookup** — `/api/admin/contacts?reg=` returns base record + DG contact details only to a valid session (same cookie + rate limit); single SQL join via the management API (account PAT), reg allowlisted before interpolation, PII excluded from logs (reg-number audit line only); `/admin` CONTACTS tab renders it, never cached (`no-store`)
- **Responsive** — single component, CSS toggles between table and cards at 768px
- **Connection status** — status pill (Busy/Live/Offline) with live clock
- **Design** — TGPC brand palette only, machine-enforced by `npm run check:colors`

---

## CI/CD

### GitHub Actions: `.github/workflows/rphsync.yml`

**Trigger:** `workflow_dispatch` (manual). Input: `force_sync` (boolean, default false).

**Single job `rphsync`** with these steps:

1. **Checkout** repository
2. **Install Python deps** (`pip install -e .` + `supabase`)
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
- `.github/workflows/ui.yml` runs on push/PR touching `ui/` — ESLint + brand-color gate (`check:colors`) + svelte-check + the 66 unit tests + a build with placeholder PUBLIC env vars (real values live in the Cloudflare Pages dashboard) + `npm audit --audit-level=high`. A second job (`e2e`) typechecks the specs (`check:e2e`) and runs the 7 Playwright suites in `ui/e2e/` on chromium. Auto-deploys from `main` build `ui/`.
- `.github/workflows/python.yml` runs on push/PR touching `tgpc/`, `tests/`, `scripts/`, or `pyproject.toml` — `ruff check`, `ruff format --check` (pinned 0.16.6, matching pre-commit), the full pytest suite, and a `pip-audit` dependency vulnerability scan. Data freshness deliberately does **not** gate pushes: it is an operational condition, so `ui/e2e/smoke.spec.ts` asserts only the health *contract* (shape, not age) — freshness is the operator's job via the daily manual `make scrape`. (The former weekly k6 `load.yml` was removed 2026-09: agent-created, unread results. The 6-hourly `health.yml` freshness poll was removed 2026-10 for the same reason: manual scrape cadence made it pure noise.)

**Dependency updates:**
Dependabot was removed (2026-09) in favour of manual bumps. CVE coverage comes from the two audit gates: `pip-audit` in `python.yml` and `npm audit --audit-level=high` in `ui.yml` — both fail the build on known-vulnerable dependencies.

---

## Testing

```bash
python3 -m pytest tests/ -v
```

195 tests across 13 files:

| File | Tests | What's tested |
|---|---|---|
| `test_scraper.py` | 24 | `_request` timeouts + clean pass-through, WAF/blocked detection (`BlockedError`), `extract_basic_records` (no table, bad rows, fallback table), `extract_detailed_info` (no records, full parse with photo/education/validity, legacy headers, missing tables), TLS verification (chain + hostname, opt-in fingerprint pin), source-host rules (apex default, `TGPC_BASE_URL` override, TGPC photo-host allowlist) |
| `test_manager_update.py` | 7 | Safety guard (90% threshold), dedup/sort/GITHUB_OUTPUT, deterministic detail ordering, source-unavailable skip, +3 regressions covering `sync_to_*` return values |
| `test_manager_enrichment.py` | 3 | Enrichment saves first pending record, raises DataIntegrityError on registration mismatch, resolves records with `serial_number = None` (M2 regression) |
| `test_manager_sync.py` | 17 | Every `sync_to_*` destination's contract: fail-closed on missing credentials, True on success, False on transport/API failure. Release test uses real pyzipper and verifies encryption at upload time; email test asserts the Resend request shape |
| `test_manager_photos.py` | 11 | Photo upload→verify→local-delete pipeline, retry/backoff, size-mismatch rejection, batch error isolation (scrape/upsert failures don't abort the batch), `retry_photos` |
| `test_quota.py` | 8 | Quota reporter helpers (ref parsing, formatting) and every `check_*` fail-closed path on missing credentials |
| `test_inactive_sweep.py` | 6 | JSONL parsing (good/bad lines), checkpoint save/load roundtrip, resume skipping completed batches, partial-run slicing |
| `test_bugfix_regressions.py` | 18 | Restore-to-temp validation, backup rotation, release return codes, `--force` overrides, DetailError vs absence |
| `test_security_audit_regressions.py` | 27 | Audit remediations: DG PII RLS posture, DG artifacts kept out of the public R2 bucket, report-email HTML escaping, rclone temp paths, photo redirect validation, backup restore validation, DG Storage bucket publicity gate |
| `test_details_dg.py` | 47 | DG caption/detail parsing, captcha handling, and the RLS/Storage posture of the DG pipeline |
| `test_dg_dashboard.py` | 19 | Local DG monitor: status merging, checkpoint-derived counts, zombie-PID detection, log tailing, IST formatting |
| `test_merge_refresh.py` | 3 | Post-scrape merged-column refresh: fill/newer-wins/skip rules, changed-columns-only upserts, fail-closed credentials |
| `test_backfill_missing.py` | 5 | Blank-only backfill contract: fills an empty column from the source, never overwrites an existing value, never blanks a column, never patches a record whose identity differs |

All tests use mocking (no real HTTP or Supabase calls). The `supabase` module is mocked globally before imports.

### Frontend

**Unit tests:** `ui/test:unit` runs `node --experimental-strip-types --test 'src/**/*.test.ts'` — 66 tests across 9 files: 18 in `auth.test.ts` (signed-cookie session creation/verification, constant-time comparison, the `isAuthed` fail-closed path with no secret), 9 in `searchUrl.test.ts` (URL ↔ search-state roundtrip), 8 in `recent.test.ts` (recently-viewed records), 8 in `searchLimits.test.ts` (every query path keeps its row cap), 6 in `exporters.test.ts` (CSV formula-injection guard), 5 each in `contacts.test.ts` and `gate.test.ts`, 4 in `fitToViewport.test.ts` (list-sizing arithmetic), 3 in `rateLimit.test.ts`. No test framework beyond Node's built-in runner.

**E2E tests:** `ui/test:e2e` runs the Playwright specs in `ui/e2e/` (chromium): smoke (shell + search flow + API locking), axe a11y (zero serious/critical violations, color-contrast excluded), a contrast-regression gate against `contrast-baseline.json`, mobile layout fingerprints at the iPhone-SE viewport, the admin drawer, and the URL-state roundtrip. `ui/test:e2e:update-baseline` refreshes the contrast baseline after intentional palette changes; `check:e2e` typechecks the specs. CI runs these in the `ui.yml` `e2e` job.

The site is a public portal, so most specs run unauthenticated; the admin surface is login-first, so the suite authenticates for it: `e2e/auth.setup.ts` logs in once via `E2E_ADMIN_SECRET` (CI repo secret, same value as Pages `ADMIN_SECRET`) into `e2e/.auth.json` (gitignored), and the admin-flow specs run on that session. Without the secret, those specs skip on gated targets while `e2e/gate.spec.ts` still asserts the unauthenticated contract (302 → /admin, 403 APIs) with a fresh context. Local `vite dev` stays open, so everything runs there except the gate specs and the credential-dependent API tests.

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
