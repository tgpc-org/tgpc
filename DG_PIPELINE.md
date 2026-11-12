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
python3 -m tgpc dg-pending --limit 600 --out data/dg_pending_ids.txt  # what to fetch
python3 -m tgpc fetch-dg --ids-file data/dg_pending_ids.txt --captcha auto --sync-cloud --sync-every 50
python3 -m tgpc fetch-dg --ids TS000001 --max-records 1   # bounded / smoke
python3 scripts/dg_captcha_bench.py --n 12                # OCR accuracy bench
make dg-local                                             # one bounded batch (same as the 01:30 job)
```
The fetch runs on the operator's Mac, because the council only answers Indian
IPs. `make dg-local` (or the nightly LaunchAgent) selects the next batch with
`tgpc dg-pending` and fetches it: retryable failures first, then fresh IDs in
serial order (the ordering contract is `tgpc/details_dg.py::pending_ids`).
The checkpoint is written after every record, so an interrupted run resumes
where it stopped. Halt cleanly with `touch data/dg_stop`.

## Code map

* `tgpc/details_dg.py` — fetcher (`DgFetcher`), parser (`parse_dg_html`),
  validators, OCR vote (`solve_captcha`, benched 10/12 first-pass), checkpoint
  runner (`run_fetch`), Supabase/R2/GDrive/SB-Storage sync.
* `tgpc/dg_migration.sql` — creates separate table `rph_dg_contacts`
  (service-role-only: captcha-gated PII, never anon-readable; no FK to `rph`
  on purpose). Re-run the whole file safely — re-running also applies the PII
  lockdown to an already-deployed project.
* `scripts/dg_local.sh` + `scripts/com.tgpc.dg.plist` — the nightly batch and
  its LaunchAgent (01:30 IST). Bounded per run, resumable, cloud-synced as it
  goes; the local files are only the crash buffer.
* `tgpc/run_status.py` — the scrape's run record (the DG batch logs to
  `data/logs/dg-local.log`).
* `tests/test_details_dg.py`.

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
R2 **private** DG bucket (`TGPC_R2_DG_BUCKET`) `dg-raw/` + `dg-contacts/`
(rolling + timestamped) + GDrive + Supabase Storage `tgpc/dg_contacts.jsonl`.
Local is a ≤50-record crash buffer only.

The R2 bucket holding DG artifacts must be a *separate, private* bucket, never
the `tgpc` bucket that serves photos and notices: that one is publicly readable
at `Config.r2_public_base`, so every key in it is world-readable. If
`TGPC_R2_DG_BUCKET` is unset — or set to the public bucket name — the push
refuses and logs an error rather than falling back.

The Supabase Storage copy is the same PII, and whether it is exposed depends on
that bucket's **public** flag — a dashboard setting, so nothing in this repo can
prove it. `push_dg_to_sb_storage` therefore asks the Storage API before every
upload and refuses when the bucket is public *or* when the flag cannot be
established: an unverifiable destination is not a private one.

## Hard rules (from production incidents)

* Single-try policy (primary objective: save whatever is available): one
  captcha attempt + one shot per HTTP call, no in-run retry loops. Misses land
  in checkpoint as transient failures for a later run. Tune via
  `--max-captcha-attempts` (default 1) only if deliberately overriding.
* Supabase writes go to `rph_dg_contacts` only — no DG code path may touch `rph`.
* `rph_dg_contacts` holds captcha-gated PII (mobile, email, home address, DOB)
  and is service_role-only. No migration may grant anon/authenticated any
  privilege on it; the pipeline is the only consumer, via the service key.
* `sync_cloud` requires `rph.json` reference (fail-closed, no orphan rows).
* Unknown/guard-failing rows → saved as-is with `raw_notes`, never
  quarantined and never silently overwritten (validate later, offline).
* `"You are not Authorized"` = per-record backend gap, refused at source
  (proven over 4 attempts/35 min) → checkpoint `failed_terminal` set
  (internal name for "refused, don't retry"), skipped on resume.
* **That refusal is not random — it tracks `date_of_registration` exactly.**
  Measured 2026-11-12 over the whole registry (91,687 rows): all 36,951
  completed records carry a council registration date, and all 54,736 refused
  records carry none. Zero exceptions in either direction. Re-probed live the
  same day (6 sampled from each side): 6/6 completed still served, 6/6 refused
  still refused, in the same session minutes apart, with the captcha accepted
  in every case — so it is a stable class, not a transient glitch, and not a
  captcha, session or egress problem.
  Practical consequence: `--retry-terminal` on the full set re-requests 54,736
  records for zero expected yield (~4 days of captchas). If DG coverage ever
  changes, prove it on a sample first:
  `fetch-dg --ids <few terminal ids> --no-resume --checkpoint /tmp/cp.json`.
  Do **not** filter the ID pool by date: the terminal set is already skipped on
  resume, and a selection rule built on a correlation would silently hide a
  change in DG's coverage.
* Valid reg prefixes: `TS|TG|TSDR|TGDR` (28k non-TS rows exist).
* Table cells parse positionally — never drop empties (empty-cell bug
  shipped header text as values once; regression-tested).
* Tests must mock ALL cloud seams (`upsert_dg_batch`, `sync_cloud_snapshot`,
  `push_file_to_r2`) — an unmocked seam once overwrote R2's rolling snapshot.
* Block markers exclude `"captcha"` (DG form pages mention it legitimately).
* WARP reconnect does NOT rotate egress IP here (verified: same IP across
  reconnect). `--warp-rotate-every N` requires a *verified different* IP every
  N records and halts (`ip_rotation_failed`) instead of proceeding unrotated.
* `--max-consecutive-blocks N` (default 25) halts on N consecutive
  `BlockedError`s (`block_storm`); saves/terminal gaps reset the counter.
* Per-record transient failures are counted in checkpoint `fail_counts`;
  after `--max-transient-retries` (default 5) the record is marked terminal
  (`retry-exhausted`, recoverable via `--retry-terminal`) so the ID pool
  provably empties. `captcha_solver_missing` never counts (environmental).
* No commits without explicit ask (repo rule); `data/` never enters git.

## Yield reference (legacy serials)

~66% enrichable, ~34% refused at source (auth-gaps), no quarantines (raw-capture
default saves everything parseable; validation deferred to `validate-dg`).
~10s/record fetch; fixed 4 workers. Captcha 83% bench, ~100% first-pass live.
