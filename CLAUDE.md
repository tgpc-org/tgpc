# CLAUDE.md — entry point for Claude Code

TGPC RPh Index: public search portal for the Telangana State Pharmacy Council
pharmacist registry — SvelteKit 3 frontend (`ui/`), Python pipeline (`tgpc/`),
Supabase, Cloudflare Pages.

**Read these first, and treat them as the source of truth.** This file
deliberately does not restate them: duplicated instructions drift (this file
used to carry its own command list, palette table and test counts — the counts
had gone stale).

1. `AGENTS.md` — working rules: mandatory brand colors, commands, workflow,
   email format.
2. `ARCHITECTURE.md` — full technical context: routes, data access, caching,
   rate limits, pipeline phases, CI.

## Non-negotiables

- **Brand colors only.** Use `TGPC` from `ui/src/lib/colors.ts`; never invent
  hex values. `cd ui && npm run check:colors` must stay at 0 offenders. Full
  palette and the contrast rule (brand hues are fills/tints/borders, small
  text uses ink tokens): `ui/src/lib/BrandColors.md`.
- **Do not commit or push unless explicitly asked.**
- **Gates before claiming done:** `cd ui && npm run check` (0 errors),
  `npm run lint`, `npm run test:unit`, `npm run check:e2e`; from the repo
  root `ruff check .`, `ruff format --check .`, `mypy`, `pytest tests/`.
- **No data keys in the browser.** Every read goes through a rate-limited
  `/api/*` proxy; SSR loaders must use SvelteKit's load `fetch`, never the
  global one (a relative URL throws on Workers — see `ARCHITECTURE.md`,
  "Data access").

## Where things live

| Area | Path |
|---|---|
| Search page, profile drawer, result exports | `ui/src/routes/+page.svelte` |
| SSR profile pages `/rph/<RPC>` | `ui/src/routes/rph/[registration_number]/` |
| Public API proxies (search, refine, record, stats, dispatch, health) | `ui/src/routes/api/` |
| Server-only helpers (auth, gate, rate limits, records, edge cache) | `ui/src/lib/server/` |
| Scrape → enrich → sync pipeline | `tgpc/` (`python3 -m tgpc update`) |
| Schema, RLS, RPC SQL | `supabase/migrations/` |

## Task-specific docs

- DG contact pipeline: `DG_PIPELINE.md`, `VPS_DG_FETCH.md`
- Security findings + resolution log: `docs/security/`
- Historical review items: `CODE_REVIEW.md`
