# CLAUDE.md — Quick Context for Claude Code

## Project

TGPC RPh Index — public search portal for the Telangana State Pharmacy Council pharmacist registry. SvelteKit 5 + Tailwind v4 + Supabase frontend (`ui/`), Python scraping/sync pipeline (`tgpc/`). Pushes to `main` auto-deploy to Cloudflare Pages (`tgpc.pages.dev`).

## Key Commands

```bash
# Python pipeline
make scrape              # Scrape → sync all destinations → enrich
make sync                # Full manual sync of rph.json
make quota               # Show free quota usage for all services

# Frontend (from ui/)
cd ui && npm run dev     # Dev server
cd ui && npm run check   # Type check (must stay at 0 errors)
cd ui && npm run lint    # ESLint (0 errors required)
cd ui && npm run check:colors  # Brand-color gate (0 offenders)

# Tests
python3 -m pytest tests/ -v   # Python tests (62 tests)
cd ui && npm run test:unit    # Frontend unit tests (18 tests)
```

## Architecture

```
tgpc/
├── tgpc/           # Python pipeline
│   ├── __main__.py # CLI entry: python3 -m tgpc {update, sync, enrich, retry-photos, quota, creds}
│   ├── scraper.py  # Scraper, RateLimiter, PharmacistRecord
│   ├── manager.py  # FileManager, BackupManager, Manager (orchestration)
│   ├── progress.py # ProgressBar, Phase, heartbeat (TTY + CI output)
│   ├── quota.py    # Free-tier quota report (Supabase, R2, Resend, GDrive)
│   ├── inactive_sweep.py  # Detect inactive→active reactivations (resumable)
│   └── enrich_actives.py  # Parallel enrichment of reactivated records
├── ui/             # SvelteKit frontend
│   └── src/
│       ├── routes/ # Pages: /, /notice, /dispatch, /admin
│       └── lib/    # Shared code, Supabase client, types
├── tests/          # Python test suite
└── data/           # Data files (gitignored)
```

## Sync Destinations

The pipeline syncs `rph.json` to:
1. Supabase (Postgres table `rph`)
2. Supabase Storage
3. Cloudflare R2
4. Google Drive (via rclone)
5. GitHub Release (encrypted zip)
6. Email notification (via Resend)

## Brand Colors (MANDATORY)

Use only TGPC brand colors in UI code:

| Token | Hex | Usage |
|---|---|---|
| tgpc green | `#00cc66` | Primary, active states |
| tgpc red | `#ef4444` | Destructive, inactive |
| tgpc grey | `#9ca3af` | Secondary text |
| tgpc blue | `#2563eb` | Links, RPC numbers |

Prefer `TGPC` export from `ui/src/lib/colors.ts`. See `AGENTS.md` for full guidelines.

## Pre-commit Hooks

- Python: `ruff check --fix` + `ruff-format`
- UI: ESLint, svelte-check, brand-color gate

## Data

~89,000 pharmacist records across 6 categories: BPharm, DPharm, MPharm, PharmD, QC, QP.

## See Also

- `AGENTS.md` — Brand colors and workflow rules
- `ARCHITECTURE.md` — Full technical documentation
- `ui/src/lib/BrandColors.md` — Color palette reference
