# DG Pipeline — getdetailsdg Contact Capture

Captures pharmacist contact details (mobile, email, DOB, home address) from the
TGPC `getdetailsdg` captcha form — a different source from the bulk
`srchpharmacisttotal` / `getsearchpharmacist` flow in `tgpc/scraper.py`.
Status: collection phase (L1 + cloud save). No UI work yet.

## Source flow (verified live)

1. `GET {base}/pharmacy/getdetailsdg` → `JSESSIONID` cookie + Struts `token`
2. `GET {base}/captchaimage.jsp` (**root path** — `/pharmacy/captchaimage.jsp` 404s)
   with same cookies + Referer → 170×50 JPEG, 6-char code
3. `POST {base}/pharmacy/getdetailsviewdg.action`
   (`registration_no`, `recapcha` [server's spelling], `struts.token.name=token`,
   `token`, `submit=Submit`) → `Pharmacist Details` table

New fields vs `rph`: `dob`, `date_of_registration`, `renewal_validity`,
`home_address/state`, `work_study_address/state`, `mobile_no`, `email_id`.
Stored verbatim `DD-MM-YYYY`; `renewal_validity` kept separate from
`validity_date` (`DD-Mon-YYYY`, UI parses it — do not merge without UI work).

## Commands

```bash
python3 -m tgpc fetch-dg --ids-file data/dg_ids_100.txt --captcha auto --sync-cloud --sync-every 50
python3 -m tgpc fetch-dg --ids TS000001 --max-records 1   # bounded / smoke
python3 scripts/dg_dashboard.py --port 8765               # live monitor → http://127.0.0.1:8765
python3 scripts/dg_captcha_bench.py --n 12                # OCR accuracy bench
touch data/dg_stop                                        # halt after current record
```
Dashboard START button (`POST /api/start`, count default 500) launches the
next batch itself: retryable failures first, then fresh IDs in serial order,
`--sync-cloud` on, one run at a time (second press refused while active).

## Code map

* `tgpc/details_dg.py` — fetcher (`DgFetcher`), parser (`parse_dg_html`),
  validators, OCR vote (`solve_captcha`, benched 10/12 first-pass), checkpoint
  runner (`run_fetch`), Supabase/R2/GDrive/SB-Storage sync.
* `tgpc/dg_migration.sql` — creates separate public table `rph_dg_contacts`
  (anon read-only RLS, no FK to `rph` on purpose). Re-run whole file safely.
* `scripts/dg_dashboard.py` + `scripts/dg_dashboard.html` — stdlib localhost
  monitor (status API, log tail, STOP button). TGPC palette only.
* `tests/test_details_dg.py`, `tests/test_dg_dashboard.py`.

## State files (all gitignored, local crash buffer only)

`data/dg_raw/{REG}.json`, `data/dg_contacts.jsonl`, `data/dg_history.jsonl`
(all-time memory, one line per record),
`data/dg_fetch_checkpoint.json` (+`failed_terminal` set),
`data/dg_stats.json`, `data/dg_live.json`, `data/dg_fetch.log`, `data/dg_stop`.
(`data/dg_quarantine.jsonl` and `data/dg_failed/` are legacy leftovers —
nothing writes them since raw-capture became the default.)

## Identifiers

`registration_number` = primary key everywhere. `serial_number` (from
`rph.json`, verified 1:1 unique, zero nulls) stamped on every artifact as the
human tracker — including `rph_dg_contacts.serial_number`.

## Cloud redundancy (all four, verified)

Supabase `rph_dg_contacts` (batch upsert every `--sync-every`, idempotent) +
R2 `dg-raw/` + `dg-contacts/` (rolling + timestamped) + GDrive + Supabase
Storage `tgpc/dg_contacts.jsonl`. Local is a ≤50-record crash buffer only.

## Hard rules (from production incidents)

* Single-try policy (primary objective: save whatever is available): one
  captcha attempt + one shot per HTTP call, no in-run retry loops. Misses land
  in checkpoint as transient failures for a later run. Tune via
  `--max-captcha-attempts` (default 1) only if deliberately overriding.
* Supabase writes go to `rph_dg_contacts` only — no DG code path may touch `rph`.
* `sync_cloud` requires `rph.json` reference (fail-closed, no orphan rows).
* Unknown/guard-failing rows → saved as-is with `raw_notes`, never
  quarantined and never silently overwritten (validate later, offline).
* `"You are not Authorized"` = per-record backend gap, refused at source
  (proven over 4 attempts/35 min) → checkpoint `failed_terminal` set
  (internal name for "refused, don't retry"), skipped on resume.
* Valid reg prefixes: `TS|TG|TSDR|TGDR` (28k non-TS rows exist).
* Table cells parse positionally — never drop empties (empty-cell bug
  shipped header text as values once; regression-tested).
* Tests must mock ALL cloud seams (`upsert_dg_batch`, `sync_cloud_snapshot`,
  `push_file_to_r2`) — an unmocked seam once overwrote R2's rolling snapshot.
* Block markers exclude `"captcha"` (DG form pages mention it legitimately).
* WARP reconnect does NOT rotate egress IP here (verified: same IP across
  reconnect). `--warp-rotate-every N` requires a *verified different* IP every
  N records and halts (`ip_rotation_failed`) instead of proceeding unrotated.
* No commits without explicit ask (repo rule); `data/` never enters git.

## Yield reference (legacy serials)

~66% enrichable, ~34% refused at source (auth-gaps), no quarantines (raw-capture
default saves everything parseable; validation deferred to `validate-dg`).
~10s/record fetch; fixed 4 workers. Captcha 83% bench, ~100% first-pass live.
