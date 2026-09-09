# TGPC RPh Index

Public-facing search portal for the Telangana State Pharmacy Council pharmacist registry. Scrapes [pharmacycouncil.telangana.gov.in](https://www.pharmacycouncil.telangana.gov.in), enriches records (photo, gender, status, validity), and serves a fast search UI.

- **Frontend:** SvelteKit 5 + Tailwind v4 + Supabase — [tgpc.pages.dev](https://tgpc.pages.dev)
- **Pipeline:** Python scraper, data enrichment, multi-destination sync (Supabase, R2, GDrive)
- **Data:** ~89,000 pharmacist records with photo, gender, registration status, and validity dates across 6 categories (BPharm, DPharm, MPharm, PharmD, QC, QP)
- **Deploys:** pushes to `main` auto-deploy to Cloudflare Pages (`tgpc-org/tgpc` → `tgpc.pages.dev`)

## Architecture

| Component | Location |
|---|---|
| Python pipeline | `tgpc/` |
| SvelteKit frontend | `ui/` |
| CI/CD | `.github/workflows/rphsync.yml` |

## Disclaimer

**NO LIABILITY.** Unofficial tool not affiliated with TGPC. Data for reference only. Operated under fair dealing (Indian Copyright Act, 1957, Section 52).

## Contribution

- **Agents/contributors:** read `AGENTS.md` first — it contains the mandatory
  TGPC brand-color guideline and repo conventions.
