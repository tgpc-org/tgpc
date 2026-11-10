# TGPC RPh Index — Design

**Status:** living document · **Last verified:** 2026-11-05
**Scope:** why the system is shaped the way it is. For file-by-file wiring, see
[`ARCHITECTURE.md`](./ARCHITECTURE.md). For contribution rules, see
[`AGENTS.md`](./AGENTS.md).

---

## 1. The problem

The Telangana State Pharmacy Council (TGPC) publishes its pharmacist register
through a form-based lookup on a government website. There is no API, no bulk
export, no stable URL for a record, and no search that a member of the public,
an employer or a pharmacist can link to.

What the source actually gives you:

| Interface | Method | What it returns |
|---|---|---|
| `/pharmacy/srchpharmacisttotal` | `GET` | One HTML table with **all** registrations (serial, RPC number, name, father's name, category) |
| `/pharmacy/getsearchpharmacist` | `POST registration_no` | One record's detail page: photo, gender, validity, status, education, work experience |
| `/site/dispatchListOfRegCert` | `GET` | A table of links to per-date dispatch-list PDFs |
| `/pdf/.../Dispatch list ....pdf` | `GET` | The PDF itself — when the council still hosts it |

That is a scrape target, not an integration. It is also a fragile one: a WAF
that intermittently blocks datacenter IPs, a certificate that once did not match
its own hostname, and a habit of deleting files it still links to.

The design problem is therefore not "build a search box". It is:

> Turn an unreliable, unaddressable, state-owned HTML form into a **stable,
> linkable, fast public index** — without ever presenting scraped data as
> authoritative, without leaking the PII the council gates behind a captcha, and
> without a human in the loop for routine operation.

---

## 2. Users and jobs

| User | Job | Design consequence |
|---|---|---|
| Member of the public | "Is this person a registered pharmacist?" | Search by name or RPC number; profile page that can be linked and crawled |
| Employer / hospital | "Verify a candidate's registration and validity" | Status, validity date, category, registration date on one screen |
| Pharmacist | "What does the council hold about me?" | Same profile page; no login, no paywall |
| Researcher / journalist | "How many BPharm registrants are active?" | Category stats, CSV/PDF export of a result set |
| Operator (single person) | "Is the index fresh and correct?" | Phased CLI, one-command pipeline, `/api/health`, run logs |
| Council-adjacent reviewers | "What are you publishing?" | Dispatch archive, notices, public-source provenance |

Non-users: nobody authenticates to browse. There is no account system, no
saved search, and no write path exposed to the public.

---

## 3. Goals and non-goals

**Goals**

1. A record is addressable — `/rph/TG061874` is a permalink with SEO metadata.
2. The index is queryable in well under a second on a mid-range phone.
3. Freshness is visible, not assumed — the shell shows last sync and a status pill.
4. The pipeline fails safe: never replace a good dataset with a bad scrape,
   never publish a half-written object, never make a dead link look alive.
5. The browser holds no database credential.
6. One operator can run the whole thing from a laptop, and CI can run it unattended.
7. Everything scraper-facing is auditable after the fact (logs, diffs, counts).

**Non-goals**

- Real-time updates. The council publishes in batches; a daily cadence is honest.
- Write-back to the council. The project is read-only against the source.
- Being an authority. The site is explicitly unofficial; every page carries that.
- Multi-tenant or multi-council operation.
- Perfect completeness. Seven council-linked PDFs 404 at source and are gone
  (see §11); the design records that rather than pretending otherwise.

---

## 4. System at a glance

```
                    ┌──────────────────────────────┐
                    │  pharmacycouncil.telangana   │
                    │  .gov.in  (HTML + PDFs)      │
                    └───────────┬──────────────────┘
                                │ scrape (WARP egress, TLS verified)
                                ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ Python pipeline  `tgpc/`                                     │
  │                                                              │
  │  update ──► validate ──► diff ──► sync ──► enrich ──► merge  │
  │     │                                              refresh  │
  │     └──► data/rph.json (local working copy, gitignored)      │
  │  dispatch ──► normalise names ──► upload missing ──► verify  │
  └───────┬───────────────┬───────────────┬──────────────┬───────┘
          │               │               │              │
          ▼               ▼               ▼              ▼
   ┌────────────┐  ┌────────────┐  ┌───────────┐  ┌────────────┐
   │  Supabase  │  │ Cloudflare │  │  GitHub   │  │  Google    │
   │ Postgres + │  │ R2 (public │  │  Release  │  │  Drive     │
   │ Storage +  │  │ photos,    │  │ encrypted │  │ (rclone    │
   │ RPC + rate │  │ dispatch,  │  │  rph.json │  │  mirror)   │
   │  limiter   │  │ notices)   │  │  .zip     │  │            │
   └──────┬─────┘  └──────┬─────┘  └───────────┘  └────────────┘
          │               │
          │ service key   │ public URLs
          ▼               ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ SvelteKit on Cloudflare Pages  `ui/`                         │
  │  /  /rph/[reg]  /notice  /dispatch  /admin                   │
  │  /api/{search,refine,record,stats,health,dispatch,notice}    │
  │  edge cache (profiles 1h, sitemaps 6h, stats 120s)           │
  └───────────────────────────┬──────────────────────────────────┘
                              ▼
                    browser (no database key)
```

Two rules explain most of the architecture:

1. **The browser never talks to the database.** Every read goes through a
   server proxy that holds the service key, re-validates input and rate-limits
   per IP (§10).
2. **The pipeline is additive and defensive.** It publishes only after
   validating, only the rows that changed, and only objects whose stored size
   matches what was uploaded (§6, §11).

---

## 5. Data model

### 5.1 `public.rph` — the registry (one row per registration)

```sql
registration_number TEXT PRIMARY KEY,
name, father_name, category TEXT,
serial_number INTEGER,          -- council's own row number, used for ordering
gender, validity_date, status TEXT,
education JSONB,                -- [{Category, Board/University, College, From, To, HT No}]
work_experience JSONB,
photo_url TEXT,                 -- set only after a verified R2 upload
date_of_registration TEXT,      -- merged from rph_dg_contacts
renewal_validity TEXT,          -- own value wins over DG
home_state TEXT
```

`rph` is the serving table and the only table the public proxies read.

### 5.2 `public.rph_dg_contacts` — captcha-gated PII, deliberately separate

```sql
registration_number TEXT PRIMARY KEY,   -- 1:1, no FK (DG must not block rph deletes)
dob, date_of_registration, renewal_validity TEXT,
home_address, home_state, work_study_address, work_study_state TEXT,
mobile_no, email_id TEXT, dg_fetched_at TIMESTAMPTZ
```

Why a separate table rather than columns on `rph`:

- The council gates these fields behind a captcha, so they are a different
  consent/intent class from the register itself.
- A DG write cannot damage a serving row.
- Access control is per table: `rph` is service-key read; `rph_dg_contacts` has
  RLS enabled **and no policies** — service role only. It is never anon-readable.

### 5.3 `public.metadata` and `public.rate_limit`

- `metadata.last_sync` — ISO timestamp the UI shows as "last sync"; the thing
  `/api/health` ages.
- `rate_limit` — shared fixed-window counters keyed by SHA-256, so it is a
  limiter and not an address log (`bump_rate_limit` RPC).

### 5.4 Object storage layout

| Store | Key | Public? | Purpose |
|---|---|---|---|
| R2 `tgpc` | `photos/{REG}.webp` | yes | Enrichment photo, q85, passport 413×531 |
| R2 `tgpc` | `dispatch/DL{DDMMYYYY}[S].pdf` | yes | Archived council dispatch lists |
| R2 `tgpc` | `notice/…` | yes | Notice attachments |
| R2 (private) | `dg/…` | **no** | DG raw artifacts; pushes refuse if the bucket is the public one |
| Supabase Storage | `tgpc/rph.json` | no | Bulk JSON snapshot |
| GitHub Release | `rphjson` → encrypted zip | no | Off-site, password-protected copy |
| Google Drive | `tgpc/rph.json` | no | Operator convenience copy |

Local artifacts (`data/rph.json`, `data/update_details.json`, sweep JSONLs,
`data/webp/`, `data/backups/`, run logs) are working state and gitignored. The
repository holds code only.

---

## 6. Ingestion: `python3 -m tgpc update`

`make scrape` runs `tgpc scrape`, which performs the update and the dispatch
capture **in one process**. That is deliberate: each CLI process connects WARP
on start and disconnects on exit, so running `update && dispatch` as two
processes tore the tunnel down between them. One process means one tunnel,
closed only when the whole run finishes.

The `update` half is five visible phases:

```
[1/5] Restore rph.json from backup
[2/5] Health check
[3/5] Backup existing data
[4/5] Scrape fresh data
[5/5] Validate & save
```

Then: sync to destinations → enrich new records → refresh merged columns.

### 6.1 Restore before scrape

If `data/rph.json` is missing or below the minimum record floor, candidates are
downloaded from R2 **newest first** into a temp file and validated: JSON list, at
or above the floor, every row an object with a non-empty `registration_number`.
A corrupt newest backup falls through to an older good one. The local registry is
never replaced by an unvalidated payload.

### 6.2 Health check, then scrape

The health check is a real request whose body is inspected for WAF/block markers
(`access denied`, `captcha`, `unusual traffic`, …), not just a status code — a
200 block page is the failure mode that matters. Only then does the bulk scrape
run: one `GET` of the total table, parsed row by row, malformed rows skipped.

### 6.3 The safety guard

The single most important rule in the pipeline:

> If the fresh scrape has **fewer than 90%** of the existing record count, abort
> with `safety_abort` and keep the old data.

A truncated HTML response, a partial WAF block or a layout change all look like
"the council deleted 60,000 pharmacists". Without this guard, one bad run
propagates a mass removal to five destinations and an email. With it, the run
fails loudly and the index stays correct.

Adjacent classifications: `source_unavailable` (timeouts, connection errors,
429/5xx) preserves data and reports; `empty_scrape` is treated as failure, not as
"everything was deleted".

### 6.4 Diffing and delta sync

After dedupe (by registration number) and sort (by serial), the run computes
new / modified / removed sets plus per-category stats and writes
`update_details.json`. Only the delta is sent to Supabase; the full artifact is
sent to R2/Drive/Release/email only when something actually changed. A no-change
run is cheap by design — verified 2026-11-04: `NEW: 0, CHANGES: 0, REMOVALS: 0`,
every network destination skipped.

### 6.5 Enrichment

Bulk rows carry identity only. Enrichment fills the rest, one record at a time:

```
POST /pharmacy/getsearchpharmacist  {registration_no}
  → validate identity (reg, name, father, category must match the row)
  → parse gender, validity, status, education, work experience, photo
  → convert photo to WebP q85, 413×531
  → upload to R2 photos/{REG}.webp, verify stored size
  → delete local copy
  → upsert the enrichment columns to Supabase
```

The identity check is load-bearing: a mismatch raises `DataIntegrityError` and
the record is **not** written. Otherwise a shifted table row could attach one
person's photo and validity to another person's registration number — the worst
possible failure for a verification tool.

Records are processed sequentially in the default path (politeness + observable
progress); `enrich_actives` uses a worker pool because reactivated records are
already known-good identities. A new-record batch above 1,000 aborts without
`--force`, so a schema change cannot trigger a 90k-record crawl by accident.

### 6.6 Reactivation sweep

An `Inactive` registration can become `Active` again. The sweep is three
resumable stages: export inactive identities → re-scrape in batches of 1,000
(checkpointed, optional parallel workers) → enrich and upsert the reactivated
ones. The checkpoint is what makes a multi-hour run survivable; a partial run
resumes rather than restarting.

### 6.7 Merged-column refresh

The last phase recomputes `date_of_registration`, `home_state` and
`validity_date` on `rph` from `rph_dg_contacts`, newest-wins, writing only rows
that still need it. It uses individual `UPDATE`s (never an upsert — an upsert
would try to INSERT a row without a `name`), and it is **warn-and-continue**: a
refresh hiccup must never fail a scrape that already succeeded.

---

## 7. Serving layer

### 7.1 Stack

SvelteKit 3 + Tailwind v4, TypeScript, deployed with `@sveltejs/adapter-cloudflare`
to Cloudflare Pages. Node is pinned by `ui/.node-version` — the same file Pages
reads — so CI and production cannot drift.

### 7.2 Pages

| Route | Rendering | Notes |
|---|---|---|
| `/` | client search | Query, refiners and sort are mirrored to the URL, so links and back/forward restore the same result set |
| `/rph/[reg]` | SSR + edge cache 1h | SEO metadata, education/work sections, sitemap-discoverable |
| `/notice`, `/dispatch` | SSR with load-`fetch` | Data fetched during SSR so crawlers and no-JS visitors see content, not an empty shell |
| `/sitemap.xml`, `/sitemap/[n].xml` | server | Index + 10k-URL chunks, active registrations only |
| `/admin` | gated | Usage, internal links, DG contact lookup |
| `/api/*` | proxies | See §7.3 |

The profile page is the SEO surface. Search itself is client-side (200-row cap),
so profiles are only crawlable because the sitemap enumerates them and the
profile route is server-rendered.

### 7.3 Data access ("Layer 2")

The browser holds **no** Supabase key:

| Browser | Proxy | Upstream |
|---|---|---|
| `searchRecords()` | `/api/search?q=&lim=` | `search_pharmacists` RPC |
| `searchWithRefiners()` | `/api/refine?…` | one capped, allowlisted PostgREST query |
| `getRecord()` | `/api/record?reg=` | one row, eleven safe columns |
| `getStats()` / `getLastSync()` | `/api/stats` | stats RPC + `metadata.last_sync` |

Each proxy is the security boundary, not the client: `lim` is clamped to 1..200
server-side, refiners are rebuilt from allowlisted enums, text is stripped of
PostgREST metacharacters, and the row ceiling is a fixed literal.

The client distinguishes *empty* from *broken*: lookups return tagged outcomes
(`ok | invalid | not-found | rate-limited | unavailable`), so "No record found"
only appears when the server truly answered 404, and a transport failure shows a
retry block instead of a false negative.

### 7.4 Caching

| Layer | TTL | Why |
|---|---|---|
| Rendered `/rph/*` HTML (`caches.default`) | 1h | A full crawl of ~77k profiles costs ~8 cold builds/day, not 77k DB reads |
| Sitemap chunks | 6h | Same reasoning; changes daily at most |
| `/api/stats` | 120s | Polled every 60s; one pair of Postgres reads per PoP per window |
| Photo/dispatch PDFs | R2 + browser | Immutable objects; no app-level cache needed |

Cache keys, security headers and the CSP nonce travel with the cached response,
so a HIT is byte-identical to a MISS (`X-Cache` reports which).

### 7.5 Search semantics

- Minimum 3 characters, 300 ms debounce.
- Ranked by prefix match first, then numeric proximity — "sharma" puts
  `Sharma` above `Kasharma`.
- Hard cap 200 rows, enforced three times: client (`searchLimits.ts`), proxy
  (`/api/search` clamp, `/api/refine` fixed literal) and the RPC's own
  `lim := least(greatest(lim,1),200)`. When the set comes back full the UI says
  it is truncated and asks for a narrower query.
- Refiners: RPC, name, father's name, gender, category, status, valid-till.
  Refiner-only searches (no text query) are valid.
- Export: CSV with a formula-injection guard, PDF via jsPDF/autotable.

---

## 8. Security model

The site is public by design; the security work is about **not** widening that.

| Concern | Control |
|---|---|
| Database key in the browser | Removed entirely; all reads go through service-key proxies |
| Abuse of search | Per-IP limiter: 120/min local budget, shared Postgres counter once the local budget is spent; strict endpoints consult the shared counter on **every** call |
| Credential brute force | 5 attempts/min, constant-time compare, fixed 250 ms delay, shared counter on every attempt |
| DG PII | Separate table, RLS on, zero policies, no anon grant; private R2 bucket required (pushes refuse to use the public bucket) |
| SSRF via photos | Photo redirects restricted to the two TGPC hosts; TLS chain + hostname verified; optional SHA-256 pin |
| XSS | Per-request CSP nonce (`script-src 'self' 'nonce-…' 'strict-dynamic'`), `nosniff`, `DENY` framing, HSTS, COOP/CORP |
| CSV injection | Cells starting with `= + - @` are neutralised |
| ReDoS / bad input | Length caps, regex-free sanitisation, allowlisted enums |
| Silent authority | Every page states the project is unofficial |

Why both a local and a shared limiter: this deployment spreads requests across
many short-lived Workers isolates, so a per-isolate window alone lets a
sequential scraper through (measured: 130 sequential requests, all 200). The
shared counter is the real limit; the local one keeps normal traffic off SQL.

---

## 9. Dependency updates

Dependabot opens grouped weekly PRs (npm `/ui`, pip root, GitHub Actions).
`dependabot-auto-merge.yml` merges any Dependabot PR once the `gate` check
passes — majors included; `dependabot-auto-close.yml` closes any Dependabot PR
whose CI fails, so a breaking bump never sits in a queue. The two together mean
success merges and failure disappears: no human review queue exists to rot.

That is a deliberate trade: audit gates only fire *after* an advisory exists, so
the cheapest moment to upgrade is immediately, and a failing bump is more
informative closed than parked.

---

## 10. Dispatch archive

The council publishes one PDF per dispatch date, linked from a report page. The
portal can only serve what is in R2, so a published list is invisible until its
PDF is copied under `dispatch/`.

Design rules, and why each exists:

| Rule | Why |
|---|---|
| Normalise every name to `DL{DDMMYYYY}[S].pdf` | The download route rejects anything else; a file stored as `Dispatch list 16-10-2026.pdf` would sit in the bucket and never appear |
| Fetch **only** what the bucket is missing | The archive is the asset; re-downloading 241 PDFs every run is waste and risk |
| Verify stored size after upload | A half-written object would serve a corrupt PDF |
| Refuse non-PDF bodies (`%PDF` magic + minimum size) | An error page saved as `.pdf` is worse than a missing file |
| A council 404 is a debug line, not a failure | Seven links 404 at source and cannot be fixed by retrying; failing the run every day trains the operator to ignore failures |
| Never delete bucket objects the council stopped linking | The council deletes its own files; the archive's job is to outlive that |
| Never rewrite an existing object without `--force` | The bucket copy is at least as trustworthy as a fresh download |

Commands: `python3 -m tgpc dispatch` (incremental), `--dry-run`, `--force`,
`--limit N`, `--write-fallback` (regenerate the listing route's stale list).

The sync is deliberately *only* fetch-and-store. It does not mirror to Drive, it
does not run a parity audit, and it does not maintain a local cache: R2 is the
serving copy and object storage is durable, so a second copy is another moving
part for no user-visible benefit. The 7 unrecoverable links are documented, not
worked around.

---

## 11. Failure modes and the intended response

| Symptom | Detected by | Behaviour | Operator action |
|---|---|---|---|
| Source blocked by WAF | Body markers in health check | `blocked`; run stops before scraping | Re-run later / adjust egress (WARP) |
| Source timeout / 5xx | `_is_source_unavailable_error` | `source_unavailable`; data preserved | Re-run |
| Partial/empty scrape | `<90%` of existing count | `safety_abort`; old data kept | Investigate; `--force` only with evidence |
| Corrupt R2 backup | `validate_rph_backup` | Falls through to older backup | None, automatic |
| Enrichment identity mismatch | Registration/name/father/category compare | Record not written, counted as error | Fix parser, re-run |
| Photo upload size mismatch | `head_object` compare | Upload rejected, local file kept for retry | `tgpc retry-photos` |
| Council PDF 404 | HTTP status | Debug log, run continues | None — unrecoverable |
| Council deletes an archived PDF | Bucket object still present | Portal keeps serving it | None |
| Supabase slow/down | `/api/health` latency + 503 | Public proxies answer 502; UI shows retry | Check status page |
| Stale data | `/api/health` `last_sync` age | `degraded` with hours | Run `make scrape` |
| R2 listing failure | `/api/dispatch` catch | Last-known list, every entry `stale: true` | Check binding/credentials |
| CI fails on a Dependabot PR | `gate` | PR auto-closed, retried next cycle | None |

Recurring principle: **every automatic path preserves the last known good
state.** There is no code path where a transient failure deletes data, blanks a
column, or publishes a partial object.

---

## 12. Quality strategy

Eight gates, run together by `make check` (it runs *all* of them, because the
question is "what did I break?", not "what failed first"):

| Gate | Guards against |
|---|---|
| `svelte-check` | type errors in Svelte/TS (runs `svelte-kit sync` first — stale `.svelte-kit` yields 51 phantom errors) |
| `eslint` | JS/Svelte defects |
| `check:colors` | off-brand hex values (0 offenders required) |
| `node:test` unit tests | URL state, limits, exporters, auth, rate limiting, SSR wiring |
| Playwright typecheck + e2e | search flow, URL roundtrip, mobile layout, a11y, contrast baseline, admin gate |
| `ruff` | lint + formatting |
| `mypy` | type errors in `tgpc/` |
| `pytest` with `--cov-fail-under=75` | behaviour + coverage erosion |

Test philosophy: **the tests pin the contracts that decide what becomes public.**
A wrong dispatch filename is a 404 the portal serves itself; an unverified photo
upload is a broken image on a verification page; an identity mismatch is another
person's data. Those get tests. Cosmetic internals do not.

Everything external is faked — no test touches the live council, Supabase or R2.
E2E runs against production for the public surface and against a signed-in
session (when `E2E_ADMIN_SECRET` exists) for `/admin`.

CI (`ci.yml`) has a `changes` job that diffs paths and runs `python`, `ui` and
`e2e` only for what changed; an unusable base or a change to `ci.yml` runs
everything, because a skipped gate looks exactly like a passing one. A single
`gate` job aggregates results and is the only required check.

---

## 13. Operations

```bash
make scrape     # update + sync + enrich + dispatch in one process, logged to data/logs/
make sync       # re-push data/rph.json without scraping
make dispatch   # capture newly published council PDFs
make quota      # free-tier usage: Supabase / R2 / Resend / Drive
make logs       # newest run log (FOLLOW=1 to tail)
make check      # all eight gates
```

`rphsync.yml` is manual (`workflow_dispatch`, optional `force_sync`). All steps
that touch the network run in **one** shell because each Actions step is a fresh
shell: the WARP tunnel was being torn down between steps, which is why the
workflow had never completed. A background loop re-connects WARP every 30 s for
the duration of update + dispatch.

Scraping needs WARP-class egress: the council blocks datacenter IPs
intermittently. Locally, `tgpc update` connects WARP only if it opened it, and
never disconnects a connection it did not create.

Observability is intentionally boring: the UI shows last sync, `/api/health`
reports database latency plus staleness, `make scrape` writes a full log and
keeps the newest 30, and the failure path emails. Scheduled probers were removed
— a 30-minute poll that alarms into an unread channel is worse than a health
endpoint you check when you care.

---

## 14. Decisions worth remembering

| Decision | Alternative rejected | Why |
|---|---|---|
| Server proxies with the service key | Ship the anon key to the browser | The browser would then need RLS to be perfect forever; a proxy is one enforcement point and lets input be re-validated |
| Service-key proxies + per-IP limits | Require login for search | A public registry lookup should not need an account |
| Postgres tables | Serve `rph.json` directly | Need ranked search, category counts, filtering, and 200-row caps without shipping 90k rows |
| Delta sync | Full re-sync every run | 90k rows/day is waste; the diff is already computed for the report |
| 90% floor abort | Trust the scrape | One truncated response would otherwise delete most of the register everywhere |
| R2 as the only dispatch copy | Local mirror + Drive + parity audit | R2 is the serving copy and durable; extra copies added moving parts without a user-visible benefit |
| Immutable dispatch filenames | Keep council filenames | The download route has one accepted grammar; normalising at capture time makes every future file serveable |
| 404s as debug, not failure | Fail the run | Seven links are permanently dead; a daily red run teaches the operator to ignore red |
| Profile HTML edge-cached | Re-query Postgres per request | 77k crawlable pages would otherwise be 77k reads/day |
| Sitemap chunks | One giant sitemap | 50k-URL protocol limit; chunking also makes caching and 503 behaviour clean |
| No scheduled health prober | 6-hourly cron | Nobody read the result; `/api/health` is the check of record |
| Auto-merge all dependency PRs | Manual review of majors | Audit gates fail only after an advisory exists; a red bump is auto-closed and retried |

---

## 15. Known limits

- **Seven dispatch PDFs are gone** (`DL17062019`, `DL11072021`, `DL25062023`,
  `DL27062023`, `DL10072023`, `DL18072023`, `DL07102026`): the council links
  them, returns 404, and they were never captured. `DL25062023` is also a council
  typo (the label says 15.06.2023). Checked against the live site, Wayback,
  Common Crawl, Drive and git history.
- **Freshness is daily, not live.** Between runs the index can be up to a day
  behind; `/api/health` says so rather than implying otherwise.
- **Search is capped at 200 rows.** A broad query is a request to narrow it, not
  a complete export; exports cover the displayed set.
- **Enrichment is the slow path.** ~90k records at one detail request each means
  a full re-enrichment is a multi-hour job; only new/changed/reactivated records
  are enriched.
- **DG contacts are incomplete by nature** — captcha-gated, one record at a time,
  with a VM doing batches and a checkpoint for resume.
- **The data is not authoritative.** It is a convenience index over a source that
  can change without notice.

---

## 16. If you are changing this

Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the exact file-level wiring, then:

1. Keep the public boundary where it is — no database key in the browser, no new
   unauthenticated endpoint that writes.
2. Keep writes additive. A new pipeline step must be safe to run twice.
3. Keep the failure posture: preserve last-known-good, fail loudly, never
   half-publish.
4. If a change touches what becomes *public*, add a test that pins it.
5. Run `make check` — all eight gates, not a subset.
6. If a change alters behaviour described here, update this file in the same
   commit.
