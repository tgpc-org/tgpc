# Code Review — TGPC RPh Index

**Date:** 2026-08-20
**Scope:** Full repo — Python pipeline (`tgpc/`, `scripts/`), SvelteKit frontend (`ui/`), CI (`.github/workflows/`), tests (`tests/`), docs
**Size reviewed:** ~4,400 lines of Python + Svelte/TS source, plus config and docs

Every finding below was verified against the source at the cited line. Findings are grouped by severity. Secrets are referenced by variable name only — no live values appear in this document.

**Remediation status (2026-08-20):** C1, C2, C3 and M7 are fixed — see "Fixes applied" at the end. Everything else is open.

---

## Summary

The pipeline is thoughtfully engineered in places: the progress/heartbeat system (`tgpc/progress.py`) is genuinely well designed, the daily-update safety guard against large record drops is exactly the right instinct, and the `source_unavailable` soft-skip path is well tested. The problems cluster in three areas.

First, the "admin" surface is not actually protected. The sensitive payload ships to every visitor in the JS bundle, and `/api/usage` fails open when its secret is unset — that endpoint holds a full-privilege Supabase PAT and can execute SQL.

Second, failures are swallowed system-wide. `sync_to_supabase` catches every exception and returns normally, no sync method returns a status, `main()` never checks one, and CI adds `|| echo "Sync failed"` on top. A total sync failure produces a green build.

Third, there is no quality gate on the frontend at all — no lint script, no `svelte-check` installed despite `AGENTS.md` mandating it, and zero JS/TS tests. The Python side has 13 solid unit tests but they never run in CI either.

`ARCHITECTURE.md` has drifted far enough from reality that it now misleads more than it documents.

---

## Critical

> **C1, C2 and C3 are fixed.** The descriptions below are retained as the record of what was wrong; see "Fixes applied" for what changed.

### C1. Sensitive admin payload ships in the public client bundle

`ui/src/routes/admin/+page.svelte:17-55`

The `groups` array is a module-level `const` inside the component script. Svelte compiles it into the client bundle unconditionally — it is present in the served JavaScript whether or not the visitor authenticates. The `{#if !authed}` gate at line 149 only controls *rendering*.

Anyone can read this by opening devtools and viewing the page's JS chunk. It includes what appear to be live TGPC reference IDs and verification tokens:

- line 21 — `viewpharmacist?referenceid=…&random_no1=…`
- line 45 — `getemailverify?rid1=…&rid2=…&rid3=…` (a UUID-shaped verification token)
- line 52 — `aconsole/adminconsole`

Two separate problems: the credential-bearing URLs are public, and the reconnaissance map of a government system's internal endpoints is public.

**Fix:** Move `groups` server-side. Return it from a `+page.server.ts` load (or a dedicated authenticated endpoint) only after verifying a session cookie. `ui/src/routes/admin/+page.server.ts` currently returns `{}` and is the natural home for this. Separately, rotate anything in those URLs that is a live token.

### C2. Client-side-only auth gate

`ui/src/routes/admin/+page.svelte:59, 67-69, 87`

```ts
let authed = $state(false);
if (import.meta.env.DEV) { authed = true; }
```

`authed` is browser state. Setting it in a debugger flips the gate. There is no cookie, no session, no server-side check on any subsequent render. Combined with C1 the attacker does not even need to bother — but this also means the login form provides no real boundary.

**Fix:** Have `/api/admin` set an `HttpOnly; Secure; SameSite=Strict` session cookie on success, and gate the data load server-side on that cookie.

### C3. `/api/usage` fails open, exposing a full-privilege Supabase PAT

`ui/src/routes/api/usage/+server.ts:105-114`

```ts
const adminSecret = env['ADMIN_SECRET'] || env['QUOTA_SECRET'];
if (adminSecret) {
  const header = request.headers.get('x-quota-secret');
  if (header !== adminSecret) return new Response('Unauthorized', { status: 403 });
}
```

If neither variable is set in the Cloudflare Pages environment, the `if` is skipped entirely and the endpoint serves the report to anyone. This is the opposite of the fail-closed behaviour in `/api/admin`, which returns 500 when unconfigured (`ui/src/routes/api/admin/+server.ts:5-7`).

The blast radius matters here: this handler uses `SUPABASE_PAT` to POST arbitrary SQL to `https://api.supabase.com/v1/projects/{ref}/database/query` (lines 33-48) and `CLOUDFLARE_API_TOKEN` against the Cloudflare account API (line 65). A Supabase PAT is an account-level credential, not a project-scoped one.

**Fix:** Invert the check — `if (!adminSecret) return 500`. Then compare in constant time. Longer term, this endpoint should not hold a PAT at all; have the pipeline write a usage snapshot to R2 and serve that.

### C4. TLS verification disabled on the scraper session, which also downloads photos *(Fixed 2026-10-21)*

`tgpc/scraper.py:102-104`

```python
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
self.session = requests.Session()
self.session.verify = False  # Site cert doesn't match hostname
```

The stated reason is a hostname mismatch on the TGPC certificate, which is a real constraint. But `verify = False` disables chain validation *and* hostname checking for every request on the session, and that same session is used to fetch photo URLs taken from scraped `img src` attributes. An active network attacker can serve arbitrary content that then flows into the WebP conversion and R2 upload path.

**Fix:** Keep chain verification and disable only the hostname check for that one host, e.g. a `requests` adapter with a custom `ssl.SSLContext` where `check_hostname = False` but `verify_mode = CERT_REQUIRED`, scoped to `pharmacycouncil.telangana.gov.in`. Photo downloads should use a separate, fully verifying session.

**Resolution (2026-10-21):** stronger than the original fix. The source's
certificate now carries a correct CN/SAN for the **apex** host
`pharmacycouncil.telangana.gov.in` (Let's Encrypt, valid to 2026-12-17), so
`Config.base_url` was switched to the apex and the hostname exception was
removed entirely. The scraper now performs **full** verification — chain *and*
hostname — for the TGPC host and every other host. `TGPC_TLS_CERT_SHA256`
remains available as an *additional* fingerprint pin (defence in depth against
a compromised CA) rather than a replacement for the hostname check.

The apex and `www.` hosts serve the same endpoints; only the apex has a
matching certificate. Photo/redirect URLs may still name either host, so
`TGPC_HOSTS` treats both as the same origin for the SSRF guard. Verified
live: 90,998 records extracted and a detail record enriched (photo + WebP +
education) with strict verification on.

**Why the apex (evidence, 2026-10-21):** the bulk listing and detail pages
are **byte-identical** across the two hosts once the per-session
`jsessionid` is normalised, and the source returns the same 200/length for
an apex URL carrying a `www.` `Referer` — so the source does not treat the
hostnames differently (no separate rate limit, session affinity, or content
split). The apex therefore costs nothing and removes the MITM gap. A
`www.` URL without a relaxed adapter fails with `SSLCertVerificationError`,
which is precisely why the default changed.

---

## High

### H1. Sync failures are invisible end to end

`tgpc/manager.py:614-615`, `tgpc/__main__.py:231-238`, `.github/workflows/rphsync.yml:88`

`sync_to_supabase` wraps its whole body in `try` and ends with:

```python
except Exception as e:
    logger.error(f"Sync failed: {e}")
```

No re-raise, no return value. The same pattern repeats across the sync methods. In `__main__.py:231-238` the `sync` command calls six sync methods in sequence and inspects none of them, so `python3 -m tgpc sync` exits 0 after failing every destination — and `Phase.__exit__` prints "— done" (`tgpc/progress.py:204`). CI then adds a third layer: `python3 -m tgpc sync || echo "Sync failed"`.

Three independent mechanisms each guarantee a green build on total failure.

**Fix:** Have each sync method return a bool or raise. Aggregate in `__main__.py` and `raise SystemExit(1)` if any destination failed. Drop the `|| echo` in the workflow.

### H2. CI cannot create releases — `contents: read`

`.github/workflows/rphsync.yml:15-17` vs. `tgpc/manager.py:896-952`

The job declares `permissions: actions: write, contents: read`. `sync_to_release()` shells out to `gh release create` / `upload` / `edit` using `GH_TOKEN: ${{ github.token }}`. Release writes require `contents: write`. This step fails in CI every run — and per H1, silently.

**Fix:** Add `contents: write` to the job's `permissions` block. Note this is also the one destination that would surface the H1 bug immediately once failures propagate.

### H3. Release zip password passed on the command line *(Fixed 2026-08-24)*

`tgpc/manager.py:915`

```python
["zip", "-e", "-j", "-P", password, archive_path, file_path]
```

`zip -P` puts the password in the process argument list, readable by any process on the host via `/proc/*/cmdline` or `ps`. On a GitHub-hosted runner the exposure window is short and the host is single-tenant, so this is a hardening issue rather than an active breach — but the same code path runs on your Mac.

Related: `tgpc/manager.py:1082` passes `Authorization: Bearer {api_key}` as a `curl` argv for the Resend call, with the same visibility problem.

**Fix:** For the zip, use `pyzipper` (AES) or feed the password via stdin. For Resend, use `requests` instead of shelling out to `curl` — you already depend on it. *(Fixed — `sync_to_release` builds an AES-256 zip in-process via `pyzipper` (added dependency); `sync_to_email` posts JSON with `requests`, dropping both the curl argv and its temp file.)*

### H4. PostgREST filter injection in search

`ui/src/lib/api.ts:16`

```ts
.or(`registration_number.ilike.%${q}%,name.ilike.%${q}%`)
```

`q` is raw user input interpolated into a PostgREST filter expression. A comma, or a `)`, lets the caller add filter clauses. RLS is SELECT-only on `rph` (per `ARCHITECTURE.md`), which caps this at unauthorized reads of that table rather than writes — real, but bounded.

**Fix:** Sanitise `q` before interpolation (strip `,().*:` and whitespace, or allowlist `[A-Za-z0-9 ]`). Better: route everything through the `search_pharmacists` RPC, which parameterises properly, and drop the `.or()` fallback.

### H5. `.limit(100000)` — unbounded result sets sent to the browser

`ui/src/lib/api.ts:8, 17, 60`

Three call sites request up to 100,000 rows: the `search_pharmacists` RPC (`lim: 100000`), the PostgREST fallback, and `advancedSearch`. `ui/src/routes/+page.svelte` then renders every row of `filtered` with no pagination or virtualisation.

A broad or empty-ish query pulls the entire registry over the wire and into the DOM. This will freeze low-end mobile browsers, and it is an easy way for anyone to run up your Supabase egress. `ARCHITECTURE.md` claims search is "paginated 50/page (25 mobile)" — the code does not do this.

**Fix:** Cap at a few hundred rows server-side, return a total count, and add real pagination or windowing. Enforce a minimum query length before hitting the network.

### H6. No security headers

`ui/static/_headers`

The file sets only cache-control for favicon and manifest. Missing: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options` / `frame-ancestors`, `Referrer-Policy`, `Strict-Transport-Security`.

For a site that renders scraped third-party content and links to a government admin console, a CSP is the cheapest meaningful mitigation available.

**Fix:** Add a global `/*` block with those headers. Start CSP in report-only mode to find breakage.

### H7. No brute-force protection on `/api/admin`

`ui/src/routes/api/admin/+server.ts:9-13`

The handler accepts unlimited POSTs with no rate limiting, no lockout, and no delay. The comparison `secret !== adminSecret` is also not constant-time. The configured secret (`QUOTA_SECRET` in `ui/.env`) is a short human-memorable password — the kind that falls to an online guessing attack in minutes at Cloudflare Workers request rates.

To be clear on the positive: `ui/.env` is correctly gitignored (`ui/.gitignore:1`) and has never been committed — verified via `git ls-files` and `git log --all`. The secret is not leaked through version control.

**Fix:** Rate-limit by IP (Cloudflare Rate Limiting rules, or a KV counter). Compare with a constant-time function. Replace the shared secret with a long random value, and rotate the current one.

---

## Medium

### M1. Deep links to `/notice` and `/dispatch` redirect to home *(Fixed 2026-08-24)*

`ui/src/routes/+layout.svelte:26-37`

```ts
const publicRoutes = ['/', '/admin'];
onMount(() => {
  if (!navigated && !publicRoutes.includes($page.url.pathname)) { goto('/'); }
});
```

`navigated` is only set by client-side navigation. On a hard load — bookmark, refresh, shared link, or a crawler — it is `false`, so any route outside the allowlist bounces to `/`. `/notice` and `/dispatch` are therefore unreachable except by clicking through from the homepage.

**Fix:** Either add both routes to `publicRoutes`, or delete this block. If the intent was to gate `/admin`, this is the wrong mechanism (see C2) — and `/admin` is in the *allowed* list, so it gates nothing. *(Fixed — the block was deleted outright along with its `goto`/`beforeNavigate`/`onMount` imports: it gated nothing since `/admin` was allowlisted and real auth is server-side post-C2. Verified: `/`, `/notice`, `/dispatch` all serve 200 in a production preview.)*

### M2. `rph_lookup` is keyed by a nullable, non-unique field — the integrity guard can corrupt data

`tgpc/manager.py:1131, 1282, 1296, 1323, 1361-1375`

```python
rph_lookup = {r.serial_number: r for r in rph_records}
...
serial = record.serial_number
basic_info = rph_lookup.get(serial)
```

`serial_number` is neither guaranteed present nor unique. `tgpc/scraper.py` sets it to `None` whenever the S.No cell is not an integer — this is deliberate and tested (`tests/test_scraper.py:69` asserts `records[0].serial_number is None`). The actual primary key is `registration_number`, which is what Supabase upserts on (`on_conflict="registration_number"`).

Two consequences. Every record with `serial_number is None` collides on the dict key `None`, so all of them resolve to whichever one appeared last. And any duplicate serial in the source data does the same.

That makes `basic_info` the *wrong record*, and it is then used for two things:

1. The "CRITICAL SAFETY CHECK" at lines 1332-1347 compares scraped name, father_name, and category against that wrong record — raising a spurious `DataIntegrityError` that aborts the entire enrichment run (line 1353).
2. Worse, if the comparison happens to pass, `basic_data` at lines 1365-1375 writes `basic_info.registration_number`, `basic_info.name`, and `basic_info.father_name` to Supabase — attributing one pharmacist's identity to another's record.

So the mechanism built to prevent data corruption is itself a corruption path, on exactly the inputs the scraper is documented to produce.

**Fix:** Key `rph_lookup` by `registration_number` at both 1131 and 1282, and look it up with `reg_no` at 1323 and 1361. Keep `serial` for logging only.

**Test gap that hid this:** `tests/test_manager_enrichment.py` uses records with distinct serials 1 and 2 and never exercises a `None` or duplicate serial. Add a case with two records whose `serial_number` is `None`.

### M3. `/api/dispatch` fabricates data on failure

`ui/src/routes/api/dispatch/+server.ts:14, 27, 32, 34-37`

The handler tries the R2 binding, then falls back to fetching its own production URL (`https://tgpc.pages.dev/api/dispatch` — a self-request that will loop in production), then falls back to `FALLBACK_DATA` with invented file sizes:

```ts
size: Math.round(50000 + Math.random() * 200000)
```

Three `catch {}` blocks discard the reasons. The client cannot distinguish real data from fiction, and the numbers change on every reload.

**Fix:** Return a 503 with an error body when the binding is unavailable. Let the UI show "unavailable". Remove the self-fetch. If placeholder entries are genuinely useful, mark them with a `stale: true` flag and omit sizes.

### M4. CSV export is vulnerable to formula injection

`ui/src/routes/+page.svelte:248-252`

Row values are joined into CSV with no escaping and no guard against leading `=`, `+`, `-`, `@`, tab, or CR. A name field beginning with `=` executes as a formula when the file is opened in Excel. Values containing commas or quotes will also corrupt the column layout.

**Fix:** Quote every field and double internal quotes; prefix any value starting with `= + - @ \t \r` with a single quote or tab.

### M5. `re` module shadowed by a local variable

`tgpc/quota.py:352`

```python
re = check_resend()
```

This rebinds the name `re` in the function scope for the rest of the function. Any later use of the `re` module in that scope raises `AttributeError`. It happens to work today only because nothing downstream uses regex.

**Fix:** Rename to `resend_usage`. Ruff's shadowing rules would catch this — see T2.

### M6. Closure over variables bound later

`tgpc/inactive_sweep.py` — `process_reg`

`process_reg` reads `out_fh` and `bar` from the enclosing scope, but both are only bound by the `with (ProgressBar(...) as bar, open(ACTIVE_FILE, 'a') as out_fh)` block that appears *after* the definition. This works only because the function is never called before that block executes. It will break the moment anyone reorders the code, and it is invisible to a reader.

Separately, `out_fh` is written to from `ThreadPoolExecutor` workers. Python file objects are not documented as thread-safe for interleaved writes; short lines usually survive, but this is not a guarantee to rely on.

**Fix:** Pass `bar` and `out_fh` as explicit parameters, or build the callable with `functools.partial` inside the `with` block. Serialise writes through a `queue.Queue` consumed by the main thread, or hold a `threading.Lock`.

### M7. The `missing_vars` warning can never fire

`ui/src/routes/api/usage/+server.ts` — `const missing: string[] = []` is declared, never appended to, and returned as `missing_vars`. The UI block at `ui/src/routes/admin/+page.svelte:242-247` that warns about unconfigured credentials is therefore dead code.

**Fix:** Populate `missing` in `checkSupabase` / `checkR2` where the `Missing …` errors are already detected, or delete both the field and the UI block. *(Fixed — `missing_vars` is now populated from the four required env vars.)*

### M8. Brand color rule violated in ~60 places *(Fixed 2026-08-24)*

`AGENTS.md` permits only `#00cc66`, `#ef4444`, `#9ca3af`, `#2563eb` plus neutrals `#111827`, `#6b7280`, `#e5e7eb`, `#f4f4f5`, `#ffffff`, `#00b359`, and explicitly forbids off-brand colors. Violations found:

- `ui/src/routes/+layout.svelte:93-95` — `statusConfig` uses `rgba(34,197,94,0.05)`, `#86efac`, `#166534`, `#22c55e`, `#fca5a5`, `#991b1b`
- `ui/src/routes/+page.svelte:462, 486` — `#000000` for Active status; also `#f3f4f6`, `#374151`, `#f0fdf4`, `#fef2f2`, `#fecaca` across lines 147-481
- `ui/src/routes/admin/+page.svelte:243` — `#fff3cd`, `#ffc107`, `#856404` (Bootstrap warning palette)
- `ui/src/routes/notice/+page.svelte:40` — `#7c3aed` (purple, for image file types)
- `ui/src/routes/dispatch/+page.svelte:88, 97, 111, 124` — `#f3f4f6`, `#f9fafb`
- `ui/src/lib/DatePicker.svelte:103-128` — `#f3f4f6`, `#f0fdf4`
- `ui/src/routes/+layout.svelte:153` and 8 sites in `admin/+page.svelte` — `#f8f9fa`

The most striking part: `AGENTS.md` says to "Prefer the `TGPC` export" from `ui/src/lib/colors.ts`, and **nothing in the codebase imports it**. Every component hardcodes hex. The rule has no enforcement mechanism, so it is decaying.

**Fix:** Decide whether the neutral greys (`#f3f4f6`, `#f8f9fa`, `#374151`, `#d1d5db`, `#f9fafb`) are actually approved — they are used consistently enough that the rule, not the code, may be what is wrong. Then fix the genuine outliers (`#22c55e`, `#166534`, `#ffc107`, `#7c3aed`, `#000000`) and add a lint rule or a Tailwind theme so the constraint is machine-checked. *(Fixed — the five greys were approved as official neutrals (owner decision) and added to AGENTS.md/BrandColors.md. Genuine outliers remapped: status pill greens/reds → `#00cc66`/`#00b359`/`#ef4444` + brand alpha tints; `#000000` → `#111827`; Bootstrap amber warning → tgpc-red tint; notice purple → `#2563eb`; `text-[#111]` → `#111827`. Machine-enforced by `ui/scripts/check-colors.mjs` via `npm run check:colors`, wired into pre-commit and CI.)*

### M9. Weak crawl-block heuristic

`tgpc/scraper.py:214-216`

```python
or len(response.text) < 1000
...
raise Exception("Blocked response")
```

Any response under 1,000 bytes is treated as a block, and the error is a bare `Exception` rather than a `TGPCError` subclass. A legitimately short page — a single-result search, an empty result — is misread as hostile. And because it is a bare `Exception`, callers cannot distinguish "blocked" from "bug in our parser".

**Fix:** Introduce `BlockedError(TGPCError)` and detect blocks from actual signals — status code, WAF marker strings, redirect target — rather than length alone.

---

## Low

### L1. Dead code

Verified by grep — no importers anywhere:

- `ui/src/lib/utils.ts` — the whole file. `fmtDate`, `fmtTime`, `fmtNumber`, `escapeHtml` are all unused. (`+page.svelte:248` calls a *locally defined* `fmtDate`.)
- `ui/src/lib/colors.ts:23` — `CATEGORY_LABELS`, and the `TGPC` export object, referenced only in `ui/src/lib/BrandColors.md:30`
- `ui/src/lib/types.ts:52` — `BadgeColor` interface
- `ui/src/routes/admin/+page.server.ts` — returns `{}`, does nothing
- `LinkItem.heading` and `LinkItem.desc` (`admin/+page.svelte:7-9`) are populated for all 12 items but never rendered

### L2. Duplication

- Identical `cachedOrNull` / `setCache` helpers copy-pasted into three files: `ui/src/routes/+layout.svelte`, `ui/src/routes/notice/+page.svelte`, `ui/src/routes/dispatch/+page.svelte`. Extract to `$lib`.
- The R2 public bucket URL `https://pub-4591c8c5282040459ade2ed1e5e3d5be.r2.dev` is hardcoded in four places: `ui/src/routes/dispatch/+page.svelte:7`, `ui/src/routes/notice/+page.svelte:45`, `tgpc/manager.py:1320`, `tgpc/enrich_actives.py:105`. Move to config/env on both sides.
- `notice/+page.svelte` and `dispatch/+page.svelte` are near-identical in structure — both are "list files from R2 with cache and icons". A shared component would remove ~100 lines.

### L3. Brittle DOM polling, and a timer that outlives the component

`ui/src/routes/+page.svelte:49-66`

A `$effect` polls every 50ms via recursive `setTimeout`, waiting for the advanced-search panel to leave the DOM, and detects it by placeholder string:

```ts
if (!document.querySelector('input[placeholder="RPC NUMBER"]')) { measureResultsBox(); }
else { measureTimer = setTimeout(waitForPanelGone, 50); }
```

The loop does terminate — that placeholder is unique to the panel input at line 341 — so this is not a runaway. But it couples layout logic to user-visible copy: change the placeholder text and the measurement silently stops working, with no error. And this effect returns no cleanup function, unlike the sibling effect at lines 68-74 which correctly disconnects its observer, so a pending `measureTimer` can fire after the component is destroyed.

**Fix:** Use `bind:this` on the panel element instead of `querySelector`, drive the remeasure off the `transition:fade` `outroend` event rather than polling, and return `() => clearTimeout(measureTimer)` from the effect. The `ResizeObserver` pattern in `admin/+page.svelte:120-139` is the right model.

### L4. `enrich_actives.py` reaches into private manager internals *(Fixed 2026-08-24)*

`tgpc/enrich_actives.py` calls `mgr._upload_and_verify_photo(...)`. It also constructs a fresh `Scraper()` per record inside `enrich()` (line ~90), discarding session reuse, connection pooling, and the adaptive rate limiter's learned state — which partly defeats the point of the `RateLimiter`.

**Fix:** Promote the photo helper to a public method. Create one `Scraper` outside the loop. *(Fixed — the method is now `Manager.upload_and_verify_photo` with all call sites updated; `enrich()` takes a scraper and each worker builds exactly one via `make_scraper()`, mirroring `inactive_sweep.py`.)*

### L5. Local `import json as _json` inside methods

`tgpc/manager.py` shadows the module-level `json` import inside several methods with a local `import json as _json`. Harmless but confusing; suggests a past name collision that was worked around rather than resolved. Remove the local imports.

### L6. `_warp_disconnect` runs twice on the happy path

`tgpc/__main__.py:198, 248-251` — registered with `atexit` *and* called in the `finally` block. The `_warp_was_connected` flag makes this idempotent, so it is correct, but the double mechanism is redundant. Keep the `atexit` handler (it covers `SystemExit(1)` at line 230) and drop the `finally`.

### L7. `ARCHITECTURE.md` has drifted substantially

The document (417 lines) describes a system that no longer exists:

- References files that are absent: `V2.md`, `UI.md`, `docs/`, `LICENSE`, `tests/sanity.py`
- Says `manager.py` is 1004 lines — it is 1405
- Describes CI steps not present in `rphsync.yml`: Supabase Storage upload, R2 upload, GDrive sync, release creation, email notification, artifact cleanup, step summary
- Omits `RELEASE_PASSWORD` from the credentials list, though it is in `CREDENTIAL_KEYS` (`tgpc/utils.py:98`) and in the workflow env
- Claims search is "paginated 50/page (25 mobile)" — the UI renders every row (see H5)

A stale architecture doc is worse than none; it sends readers looking for behaviour that was removed.

**Fix:** Cut it back to what you will actually maintain — the data flow, the credential list, and the sync destinations. Drop the line counts and the file inventory, which go stale by design.

---

## Testing

### T1. Python tests are decent but never run

Three files, 13 tests, 646 lines:

- `tests/test_scraper.py` — 7 tests. Good coverage of HTML parsing: table fallback, malformed rows, legacy education headers, missing tables, the split connect/read timeout contract, base64 photo extraction.
- `tests/test_manager_update.py` — 4 tests. Strong. Covers the large-drop safety guard, dedup and sort ordering, deterministic `GITHUB_OUTPUT` detail ordering, and the `source_unavailable` soft-skip.
- `tests/test_manager_enrichment.py` — 2 tests. Covers sorted upsert order and the `DataIntegrityError` registration-mismatch guard.

The quality is real — the determinism assertions in `test_update_outputs_deterministic_detail_order` are the kind of thing most projects skip. But `.github/workflows/rphsync.yml` has no test step. Nothing runs these except manually.

I could not execute the suite in this environment: `pytest` is not installed and the sandbox has no network access to install it (`pip` failed with a proxy 403). The findings above are from reading, not from a run.

**Fix:** Add a `test` job to CI, or a separate workflow on push/PR, running `python3 -m pytest tests/ -q` and `ruff check .`.

### T2. Frontend has no tests and no quality gate

- `ui/package.json:6-10` — scripts are `dev`, `build`, `preview` only. No `lint`, no `check`, no `test`.
- `svelte-check` is not in `devDependencies` and not present in `ui/node_modules/.bin`, despite `AGENTS.md` instructing `npx svelte-check --threshold error`. The documented gate cannot be run as written.
- `.pre-commit-config.yaml` runs `ruff` and `ruff-format` only — zero JS/TS/Svelte checks.
- No ESLint, no Prettier, no component or unit tests.

This is the gap that let M1, M7, and the brand-color decay through. `platform?.env` is also cast with `as Record<string, string>` in both API handlers, which discards the `| undefined` from `app.d.ts:3` — a type check would flag it.

**Fix:** Add `svelte-check` and `typescript` to devDependencies with a `check` script; add `eslint` + `eslint-plugin-svelte` with a `lint` script; wire both into pre-commit and CI.

### T3. Coverage gaps in the Python tests *(Sync layer covered 2026-08-24)*

Untested paths that carry real risk: the entire sync layer (`sync_to_supabase`, `sync_to_r2`, `sync_to_release`, `sync_to_email`, `sync_to_gdrive`), `retry_photos`, `_process_records_sequential`, `inactive_sweep.py`, `quota.py`, `enrich_actives.py`, and the photo upload/verify path. M2 sits in `_process_records_sequential`, which has no direct test at all.

*(2026-08-24: the sync layer is now covered — `tests/test_manager_sync.py`, 17 tests across all five `sync_to_*` destinations plus Supabase Storage, asserting fail-closed on missing credentials, True on success, False on transport/API failure. The release test uses real pyzipper and verifies encryption at upload time; the email test asserts the Resend request shape after the H3 requests migration.)*

*(2026-08-25: remaining gaps closed — `tests/test_manager_photos.py` (11 tests) covers the photo upload/verify/retry pipeline and batch error isolation; `tests/test_quota.py` and `tests/test_inactive_sweep.py` cover the quota reporter's fail-closed paths and the sweep's JSONL/checkpoint/resume plumbing. Suite at 62 tests. Still untested: live network behavior itself, by design.)*

---

## What is working well

Worth stating explicitly, because a review of this shape reads more negatively than the codebase deserves:

`tgpc/progress.py` is the standout — TTY and non-TTY paths, a heartbeat thread so long `subprocess.run` calls never go silent, and a logging handler that clears and redraws the bar so log lines never corrupt it. That is a well-understood problem solved properly, in pure stdlib.

The daily-update safety guard that refuses a >20% record drop (`test_safety_guard_blocks_large_drop`) is the right defensive instinct for a scraper against a source you do not control. The `source_unavailable` soft-skip distinguishes "the site is down" from "the data changed," which many pipelines conflate. `DataIntegrityError` on registration-number mismatch during enrichment catches a genuinely dangerous class of bug.

Credential handling via macOS Keychain with an env-var override and a file fallback (`tgpc/utils.py:104-165`) is a sensible layering, and `ui/.env` really is properly gitignored.

---

## Suggested order of work

1. ~~**C1 + C2**~~ — done.
2. ~~**C3**~~ — done.
3. ~~**M2**~~ — done (re-key `rph_lookup` to `registration_number`).
4. ~~**H1 + H2**~~ — done (sync failures propagate via `SystemExit(1)`; `contents: write` in CI).
5. ~~**H4 + H5**~~ — done (input sanitised; results capped at 500 + client-side pagination).
6. ~~**T2**~~ — done (svelte-check + ESLint in pre-commit and CI).
7. ~~**H6, H7, C4**~~ — done (security headers, login rate limiting + 429, scoped TLS).
8. ~~**L7**~~ — done (trimmed stale sections of `ARCHITECTURE.md`).

> Remaining: rotate the exposed admin-link tokens (manual step above) and replace the short `QUOTA_SECRET` with a long `ADMIN_SECRET` in the Cloudflare Pages dashboard. The in-code hardening is complete; these are secret-management actions.

---

## Fixes applied — 2026-08-20

### Still requires manual action

**Rotate the exposed TGPC credentials.** C1 made these public in the served JavaScript for as long as the site has been deployed. Moving them server-side stops future exposure but cannot undo past exposure. Treat as compromised:

- the `referenceid` / `random_no1` pair in the `viewpharmacist` URL
- the `rid1` / `rid2` / `rid3` verification tokens in the `getemailverify` URL

*(2026-08-24: the inline fallbacks have been deleted from `ui/src/lib/server/adminLinks.ts` — no token remains in version control or in the built output. The two links now render only when `ADMIN_LINK_PHARMACIST_URL` / `ADMIN_LINK_EMAIL_VERIFY_URL` are set in Cloudflare Pages. Rotation on the TGPC side and setting those env vars remain manual.)*

**Replace `QUOTA_SECRET`.** It is a short human-memorable password. Set a long random `ADMIN_SECRET` in the Cloudflare Pages dashboard; it takes precedence and rotating it invalidates outstanding sessions by design. (Rate limiting, which was the H7 concern, is now implemented; `/api/admin` returns HTTP 429 after 5 failed attempts per minute, IP-wise.)

### Files changed

| File | Change |
|---|---|
| `ui/src/lib/server/auth.ts` | New. Signed-cookie session helpers: `createSession`, `verifySession`, `isAuthed`, `getAdminSecret`, constant-time `safeEqual`. |
| `ui/src/lib/server/adminLinks.ts` | New. The admin link list, moved out of the component; env-overridable for the two token-bearing URLs. |
| `ui/src/lib/server/auth.test.ts` | New. 18 tests, no new dependencies. |
| `ui/src/routes/admin/+page.server.ts` | Gates the payload on a verified session; returns `groups: []` when unauthenticated. |
| `ui/src/routes/admin/+page.svelte` | Consumes `data.authed` / `data.groups`; local `groups` const and `authed` state removed. |
| `ui/src/routes/api/admin/+server.ts` | Constant-time compare; issues an HttpOnly session cookie; `DELETE` for logout. |
| `ui/src/routes/api/usage/+server.ts` | Fails closed; accepts session cookie or header; typed handler; populates `missing_vars`. |
| `ui/src/lib/types.ts` | Added `LinkItem` / `LinkGroup`. |
| `ui/package.json` | Added `test:unit` script. |
| `ui/tsconfig.json` | Added `allowImportingTsExtensions` (needed for Node's type-stripping test runner). |

### How each was addressed

**C1** — the link list moved to `ui/src/lib/server/adminLinks.ts`. SvelteKit fails the build if anything under `$lib/server/` is reachable from client code, so this is enforced by the compiler rather than by convention. `admin/+page.server.ts` includes it in the load response only for an authenticated session.

**C2** — `authed` is no longer client state. `/api/admin` issues an `HttpOnly; SameSite=Strict; Secure` cookie holding `exp.HMAC-SHA256(secret, "v1." + exp)`, and every read re-verifies signature and expiry server-side. Because the admin secret is the signing key, rotating it invalidates all sessions. The `import.meta.env.DEV` bypass moved to the server load as `dev` from `$app/environment`, which Vite statically replaces — so it is eliminated from production output rather than merely unreachable.

**C3** — the `if (adminSecret)` wrapper became `if (!adminSecret) return 500`. Comparison is now constant-time. The client no longer holds the raw secret in memory or sends it as a header; the cookie carries authorization. The `x-quota-secret` header still works for non-browser callers.

### Verification

- `npm run test:unit` — 18/18 pass, including an explicit regression guard that `isAuthed` returns `false` when no secret is configured (C3) and that a client-extended expiry is rejected.
- `npx tsc --noEmit` — no errors in any changed file. Three pre-existing `TS7031` errors remain in `api/dispatch/+server.ts` and `api/notice/+server.ts`, which were not touched.
- All 7 Svelte components compile; `admin/+page.svelte` compiles with zero warnings.
- The compiled **client** output of `admin/+page.svelte` was searched for `viewpharmacist`, `getemailverify`, `adminconsole` and both live token values — none are present. This is the direct confirmation that C1 is closed.

`vite build` could not be run in the review environment: `ui/node_modules` was installed on macOS and the Linux sandbox has no matching rollup binary, with no network access to fetch one. **Please run `npm run build` locally before deploying.** The changes are type-checked and compile-checked, but have not been through a full bundle.


---

*Nothing was committed or pushed. Source changes are limited to the files listed under "Fixes applied"; no other file in the repository was modified.*

---

## Fixes applied — 2026-08-24

| Finding | Change |
|---|---|
| M1 | Deleted the broken deep-link redirect (`publicRoutes`/`navigated`/`onMount` block) from `ui/src/routes/+layout.svelte`; `/notice` and `/dispatch` now load on hard navigation (verified 200 in a production preview). |
| H3 | `sync_to_release` builds an AES-256 zip via `pyzipper` (new pinned dependency) — no password in argv. `sync_to_email` uses `requests` instead of curl — no API key in argv, no temp file. |
| C1 follow-up | Inline token fallbacks removed from `ui/src/lib/server/adminLinks.ts`; credential-bearing links appear only from `ADMIN_LINK_PHARMACIST_URL` / `ADMIN_LINK_EMAIL_VERIFY_URL` env vars. Repo-wide grep confirms no token values remain. |
| M8 | Off-palette colors remapped to the TGPC palette (soft tints via brand rgba); five Tailwind-grey neutrals approved and documented in AGENTS.md/BrandColors.md; new machine gate `npm run check:colors` (`ui/scripts/check-colors.mjs`) wired into pre-commit and CI. |
| L4 | `Manager.upload_and_verify_photo` is now public; `enrich_actives.py` builds one Scraper per worker instead of per record. |
| T3 (partial) | New `tests/test_manager_sync.py`: 17 tests covering all sync destinations' fail-closed/success/failure contracts, including a real-pyzipper encryption check on the release archive. Suite now 37 tests. |

**Verification:** Python suite 20/20 pass; UI eslint 0 errors, svelte-check 0 errors, 18/18 unit tests, production build OK; pyzipper round-trip (encrypted write + password read + no-password rejection) smoke-tested.
