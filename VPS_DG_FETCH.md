# DG Fetch on a cloud VPS — runbook

Runs the `getdetailsdg` captcha fetch on a small VM instead of the Mac.
Same pipeline, same destinations (Supabase `rph_dg_contacts` + R2 private
bucket + GDrive + SB Storage). DG data never touches prod `last_sync`.

DG fetch moves ~60 KB/record (captcha + details page + cloud upserts — no
photos; those belong to enrich, which stays local). 1,000 records ≈ 60 MB
egress, and raw snapshots are ~4 KB each (~350 MB for all 89k). Any micro
VM comfortably fits this.

## 0. Prereqs (Mac side)

- Oracle Cloud account with Always Free eligibility.
- These files from this repo on the Mac (for one-time seeding):
  `data/dg_fetch_checkpoint.json` (keeps ~9k terminal skips).

## 1. Provision the VM (console, one-time, ~10 min)

Pick ONE provider:

**A. GCE `e2-micro` free tier (recommended — actually free)**
1. `console.cloud.google.com` → new project → **Compute Engine → VM instances
   → Create instance** (billing account with card required; e2-micro + 30 GB
   disk + 1 GB egress/mo is $0 in `us-west1` / `us-central1` / `us-east1`)
2. Name: `tgpc-dg-fetch` · Region: `us-west1` (any zone) · Machine type: **E2
   → `e2-micro` (1 vCPU, 1 GB)** · Boot disk: **Ubuntu 24.04 LTS, 30 GB**
3. Firewall: defaults (SSH allowed, nothing else needed — the fetch only
   makes outbound connections). Leave preemptibility OFF.
4. Note the username you SSH as (browser SSH uses your gmail prefix, or add
   your key under Metadata → SSH Keys for `ssh <user>@<ip>`).
5. Copy the external IP.
6. Egress budget: ~16k records/month free. Watch it under Billing while the
   first batch runs; if destinations push you over, the overage is cents.

**B. Oracle Always Free `VM.Standard.A1.Flex` (4 OCPU / 24 GB)**
Ubuntu 24.04 image, public IP, ingress 22/tcp only. Scripts assume user
`ubuntu` + checkout at `~/tgpc`. Known pain: signup rejections and
"Out of capacity" on A1 shapes — retry another AD or abandon for option A/C.

**C. Hetzner CAX11 (~€4.15/mo, painless signup)**
Ubuntu 24.04, Nuremberg/Falkenstein. You land as `root`, not `ubuntu`.

**Path fixups when your user isn't `ubuntu`** (options A/C): the scripts and
service file say `/home/ubuntu` — on the VM, before installing the unit:
`sed -i 's|/home/ubuntu|/root|g; s/^User=.*/User=root/' ~/tgpc/scripts/tgpc-dg-fetch.service`
(substitute your actual home dir). `vps_bootstrap.sh` uses `$HOME`/`~` and
needs no changes.

## 2. Bootstrap (on the VM, one-time)

```bash
# from the Mac — or paste the script over:
scp scripts/vps_bootstrap.sh ubuntu@<vm>:~/
ssh ubuntu@<vm> 'bash ~/vps_bootstrap.sh'
```

This installs `tesseract-ocr` (captcha OCR), `rclone`, `cloudflare-warp`
(tunnel — mandatory: GCE IPs are source-blocked, proven 2026-09-30 with 0/20
direct), clones the repo, `pip install -e .`, pulls `data/rph.json` from
Supabase Storage, and creates `~/.tgpc_env` (chmod 600) with placeholders.
WARP is installed + registered but left DOWN — the fetch script connects per
run (autostart on a headless box blackholes SSH).

Fill in `~/.tgpc_env` — same values as the Mac Keychain / `rphsync.yml`
secrets: `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `CLOUDFLARE_ACCOUNT_ID`,
`TGPC_R2_DG_BUCKET=tgpc-dg-private`, optional `RCLONE_GDRIVE_CONFIG`.

SSH here and everywhere in this repo goes through `gcloud compute ssh`
(OS Login certs, minted transparently) whenever gcloud can resolve the VM
by its static IP, falling back to direct `ssh user@host` otherwise.

## 3. Seed the checkpoint (Mac side, one-time)

```bash
scp data/dg_fetch_checkpoint.json ubuntu@<vm>:~/tgpc/data/
```

Without this the VM re-probes every terminal record (~9k wasted fetches).
(`dg_stats.json` / contacts jsonl are optional — cloud already holds the data;
the fetch script also restores the checkpoint from the R2 `ops/` backup if the
local file is missing.)

## 4. Smoke test (on the VM — DO NOT skip)

Recurring runs skip the manual steps below: `./scripts/dg_run.sh` (set
`DG_VM=user@host` once) does preflight, drift guards, input push, env,
smoke-gated start and verification in one command. The manual sequence
remains documented for first-time/debugging use.

```bash
cd ~/tgpc && ./scripts/vps_fetch.sh --smoke
```

50 records via WARP. **Pass bar:** `+N saved` delta > 0 with no
`BlockedError` streak, and raw snapshots appearing in the private bucket.

## 5. Long run (on the VM)

```bash
sudo cp ~/tgpc/scripts/tgpc-dg-fetch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tgpc-dg-fetch
```

Loops 1000-record batches until the ID pool is exhausted:
`--sync-cloud --sync-every 50`.
No `--warp-rotate-every`: consumer WARP egress is sticky per account, so the
rotation gate can never see a *different* IP and halts every 500 records
instead of protecting anything. The per-run tunnel (masked egress) is the
actual protection; blocks are monitored via fail reasons.
Workers: `TGPC_DG_WORKERS` in `~/.tgpc_env` (default 8, max 16 — raise in
steps and watch for `BlockedError` streaks; back off at the first one).
Checkpoint + stats are pushed to `tgpc-dg-private/ops/` after every batch.

## 6. Ops from the Mac

| Need | Command |
|---|---|
| Progress | `ssh ubuntu@<vm> 'python3 -c "import json;s=json.load(open(\"tgpc/data/dg_stats.json\"));print(s.get(\"done\"),s.get(\"failed\"),s.get(\"fail_by_reason\"))"'` |
| Dashboard | `ssh -L 8899:localhost:8899 ubuntu@<vm>` → `~/tgpc` → `python3 scripts/dg_dashboard.py --port 8899` → open `http://localhost:8899/` |
| Clean stop | `./scripts/vps_ctl.sh halt [note]` (sets the R2 halt flag, loop pauses after current batch) or `touch ~/tgpc/data/dg_halt` (halts after current batch). Resume: `./scripts/vps_ctl.sh resume`, or `sudo systemctl restart tgpc-dg-fetch`. |
| No-SSH ops | `scripts/vps_ctl.sh` (Mac) drives the loop over R2 `ops/ctl.json` — the loop polls it every batch: `status` (progress from R2 backups), `halt [note]` / `resume` (pause without SSH/systemd), `exclude CIDR...` (live WARP exclusions), `show`. A broken control read fails OPEN (never stops fetching). |
| Egress IP | `curl -s https://api.ipify.org` during a run (must differ from the VM's external IP = masked via WARP) |
| SSH survival | Operator IPs in `TGPC_SSH_EXCLUDE` (`~/.tgpc_env`) bypass the tunnel; tunnel is DOWN between runs (disconnect-on-exit trap) |
| Logs | `journalctl -u tgpc-dg-fetch -f` + `~/tgpc/data/dg_fetch.log` |

## Troubleshooting

- **Locked out after WARP connects (SSH timeout):** an active tunnel reroutes
  the default gateway and blackholes inbound SSH. Headless discipline (in the
  scripts): `warp-svc` daemon runs but NEVER autostarts the tunnel;
  `vps_fetch.sh` connects per run, excludes `TGPC_SSH_EXCLUDE` IPs, and
  disconnects on exit. If locked out anyway: Stop the VM, set a startup
  script with `systemctl disable --now warp-svc` + `warp-cli disconnect`,
  Start.
- **Old `warp-cli register` fails:** 2026.x clients use `warp-cli
  --accept-tos registration new` and `warp-cli --accept-tos connect` —
  `register` no longer exists. Bootstrap uses the new syntax.
- **All-terminal run (Not authorized streak):** normal in gap ranges — the loop
  skips them via checkpoint and keeps going. Only worry on `unexpected` /
  `BlockedError` streaks.
- **Halt with `stop_reason: block_storm`:** circuit breaker tripped on 25
  consecutive `BlockedError`s (filtering, not gaps) — the loop stays stopped
  by design. Investigate (tunnel up? egress changed?), then `sudo systemctl
  restart tgpc-dg-fetch` to resume. Tune via `--max-consecutive-blocks`
  (0 disables).
- **`captcha_solver_missing`:** tesseract binary or `pytesseract` missing —
  re-run the bootstrap apt/pip steps.
- **R2 `AccessDenied` on `ops/` backup:** R2 keys lack rights on the DG bucket;
  main fetch still works, only the checkpoint backup fails (fix keys, `scp`
  the checkpoint back as fallback).
- **VM reboot:** systemd restarts the loop; checkpoint is on persistent disk.
- **GDrive slice skipped:** `RCLONE_GDRIVE_CONFIG` unset or `rclone` missing —
  by design, Supabase + R2 still sync.

## Cost note

e2-micro free tier (1 vCPU/1 GB, 30 GB disk, 1 GB egress/mo in US regions)
fits DG-fetch-only comfortably: ~60 KB/record ≈ 16k records per free GB, raw
snapshots ~4 KB each. Only metered risk is R2 Class-A writes — same as Mac
runs today.
