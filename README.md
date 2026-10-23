# TGPC RPh Index

Unofficial, free search portal for the Telangana State Pharmacy Council (TGPC)
pharmacist registry — **[tgpc.pages.dev](https://tgpc.pages.dev)**.

The council publishes its register through a form-based lookup. This project
scrapes it into a queryable dataset — **91,048 registrations** as of 2026-10-23
(77,049 active, 13,998 inactive, 1 unstatused) — enriches every record with
photo, gender, validity and status, and serves it through a fast, mobile-first
search UI with per-record profile pages.

## What it does

- **Search** by name or RPC number (e.g. `TG061874`), debounced, ranked by
  prefix match; refine by RPC / name / father's name / gender / category /
  status / validity; filter by six categories (BPharm, DPharm, MPharm, PharmD,
  QC, QP); sort any column; export the current result set as CSV or PDF.
- **Profile pages** at `/rph/<RPC>` — server-rendered, indexed, edge-cached.
  The same record opens in a drawer from the results list.
- **Notices** and **dispatch lists** from the council, with PDF links.
- **Sitemap** (`/sitemap.xml`) covering every active registration, so profiles
  are crawlable even though search itself is client-side.
- **Health + uptime**: `/api/health` reports database latency and data
  freshness; a scheduled workflow opens one GitHub issue while the site is
  down and closes it on recovery.

## Stack

| Piece | Technology |
|---|---|
| Frontend | SvelteKit 3, Tailwind CSS v4, TypeScript |
| Data | Supabase (Postgres + RPC + Storage) |
| Files | Cloudflare R2 (photos, dispatch PDFs) |
| Hosting | Cloudflare Pages (push to `main` deploys) |
| Pipeline | Python 3.12 (CI), requests + BeautifulSoup + supabase-py |
| Tests | `pytest` (210), `node:test` (107), Playwright e2e |

## Repository layout

```
tgpc/
├── ui/                  # SvelteKit app (search, profiles, notices, dispatch, admin)
│   └── src/
│       ├── routes/      # pages + /api/* proxies + sitemaps
│       └── lib/         # shared client code, server-only helpers, tests
├── tgpc/                # Python pipeline: scrape → enrich → sync
├── scripts/             # DG contact fetch tooling, VM/VPS helpers, uptime check
├── tests/               # Python test suite
├── supabase/migrations/ # Schema, RLS, RPCs, shared rate limiter
├── docs/security/       # Audit findings + resolution log
└── ARCHITECTURE.md      # Technical documentation (single source of truth)
```

## Local development

### Frontend

```bash
cd ui
npm ci
npm run dev            # http://localhost:5173
```

Node is pinned to the version in `ui/.node-version` (24.x) — the same file
Cloudflare Pages reads, so CI and production cannot drift.

Public variables are resolved at runtime (`ui/src/env.ts`), so the dev server
needs no secrets; the `/api/*` proxies read `SUPABASE_URL` and
`SUPABASE_SECRET_KEY` from the environment.

### Pipeline

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
make scrape            # scrape → sync every destination → enrich new records
make sync              # re-sync data/rph.json without scraping
make quota             # free-tier usage for Supabase / R2 / Resend / Drive
make logs              # newest run log (FOLLOW=1 to tail it)
```

Credentials come from environment variables, then the macOS Keychain, then a
local file (see `tgpc/utils.py`). Data artifacts live under `data/` and are
gitignored — the repository holds code only.

## Checks

```bash
# Frontend (from ui/)
npm run check          # svelte-check, must stay at 0 errors
npm run lint           # eslint
npm run check:colors   # brand-color gate, must stay at 0 offenders
npm run test:unit      # node:test
npm run check:e2e      # type-check the Playwright specs
npm run test:e2e       # Playwright against production (PROD_URL to override)

# Python (from the repo root)
ruff check . && ruff format --check .
pytest tests/ --cov --cov-fail-under=65   # coverage gate, see pyproject.toml
```

CI runs the same gates on every push to `main`/`dev` (`.github/workflows/`),
plus `npm audit` / `pip-audit` dependency scans and a 30-minute uptime probe.
Dependabot groups weekly minor/patch updates.

## Documentation

| Document | Contents |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Everything: routes, data access, caching, rate limits, pipeline phases, CI |
| [`AGENTS.md`](./AGENTS.md) | Rules for contributors and AI agents (brand colors, commands, email format) |
| [`CLAUDE.md`](./CLAUDE.md) | Entry point for Claude Code — points at the two documents above |
| [`DG_PIPELINE.md`](./DG_PIPELINE.md) | DG contact-capture pipeline (captcha flow, dashboard, CLI) |
| [`VPS_DG_FETCH.md`](./VPS_DG_FETCH.md) | Runbook for running the DG fetch on a VPS |
| [`CODE_REVIEW.md`](./CODE_REVIEW.md) | Historical review findings and their resolutions |
| [`ui/src/lib/BrandColors.md`](./ui/src/lib/BrandColors.md) | Brand palette and contrast-safe usage |

## Disclaimer

**NO LIABILITY.** This is an unofficial, third-party tool — not affiliated with
TGPC or any government body. Data is for reference only; verify everything
against official sources before relying on it. Operated under fair dealing
(Indian Copyright Act, 1957, Section 52).
