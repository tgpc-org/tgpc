# DG Fetch on Oracle Always Free VPS — runbook

Runs the `getdetailsdg` captcha fetch on an ARM VM instead of the Mac.
Same pipeline, same destinations (Supabase `rph_dg_contacts` + R2 private
bucket + GDrive + SB Storage). DG data never touches prod `last_sync`.

## 0. Prereqs (Mac side)

- Oracle Cloud account with Always Free eligibility.
- These files from this repo on the Mac (for one-time seeding):
  `data/dg_fetch_checkpoint.json` (keeps ~9k terminal skips).

## 1. Provision the VM (console, one-time, ~10 min)

- Shape: `VM.Standard.A1.Flex`, **4 OCPU / 24 GB RAM**, Oracle Linux→ switch to
  **Ubuntu 24.04** image. VNIC: public IP, ingress **22/tcp only**.
- SSH in as `ubuntu`. The scripts assume user `ubuntu` + checkout at `~/tgpc`
  (edit `User=`/`WorkingDirectory=` in `scripts/tgpc-dg-fetch.service` if yours differ).

## 2. Bootstrap (on the VM, one-time)

```bash
# from the Mac — or paste the script over:
scp scripts/vps_bootstrap.sh ubuntu@<vm>:~/
ssh ubuntu@<vm> 'bash ~/vps_bootstrap.sh'
```

This installs `tesseract-ocr` (captcha OCR), `rclone`, `cloudflare-warp`,
connects **WARP (mandatory — the source blocks datacenter IPs)**,
clones the repo, `pip install -e .`, pulls `data/rph.json` from Supabase
Storage, and creates `~/.tgpc_env` (chmod 600) with placeholders.

Fill in `~/.tgpc_env` — same values as the Mac Keychain / `rphsync.yml`
secrets: `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `CLOUDFLARE_ACCOUNT_ID`,
`TGPC_R2_DG_BUCKET=tgpc-dg-private`, optional `RCLONE_GDRIVE_CONFIG`.

## 3. Seed the checkpoint (Mac side, one-time)

```bash
scp data/dg_fetch_checkpoint.json ubuntu@<vm>:~/tgpc/data/
```

Without this the VM re-probes every terminal record (~9k wasted fetches).
(`dg_stats.json` / contacts jsonl are optional — cloud already holds the data;
the fetch script also restores the checkpoint from the R2 `ops/` backup if the
local file is missing.)

## 4. Smoke test (on the VM — DO NOT skip)

```bash
cd ~/tgpc && ./scripts/vps_fetch.sh --smoke
```

50 records, no WARP rotation. **Pass bar:** `done > 0` in the printed stats
and raw snapshots appearing in the private bucket. If you see a `BlockedError`
streak or 0 saved, WARP egress isn't dodging the block — stop here, the idea
is dead on this provider.

## 5. Long run (on the VM)

```bash
sudo cp ~/tgpc/scripts/tgpc-dg-fetch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tgpc-dg-fetch
```

Loops 1000-record batches until the ID pool is exhausted:
`--sync-cloud --sync-every 50 --warp-rotate-every 500 --warp-max-cycles 3`.
Checkpoint + stats are pushed to `tgpc-dg-private/ops/` after every batch.

## 6. Ops from the Mac

| Need | Command |
|---|---|
| Progress | `ssh ubuntu@<vm> 'python3 -c "import json;s=json.load(open(\"tgpc/data/dg_stats.json\"));print(s.get(\"done\"),s.get(\"failed\"),s.get(\"fail_by_reason\"))"'` |
| Dashboard | `ssh -L 8899:localhost:8899 ubuntu@<vm>` → `~/tgpc` → `python3 scripts/dg_dashboard.py --port 8899` → open `http://localhost:8899/` |
| Clean stop | `ssh ubuntu@<vm> 'touch ~/tgpc/data/dg_halt'` (halts after current batch) then `sudo systemctl stop tgpc-dg-fetch` |
| WARP check | `warp-cli status` must say Connected; egress IP via `curl -s https://api.ipify.org` |
| Logs | `journalctl -u tgpc-dg-fetch -f` + `~/tgpc/data/dg_fetch.log` |

## Troubleshooting

- **All-terminal run (Not authorized streak):** normal in gap ranges — the loop
  skips them via checkpoint and keeps going. Only worry on `unexpected` /
  `BlockedError` streaks.
- **`captcha_solver_missing`:** tesseract binary or `pytesseract` missing —
  re-run the bootstrap apt/pip steps.
- **R2 `AccessDenied` on `ops/` backup:** R2 keys lack rights on the DG bucket;
  main fetch still works, only the checkpoint backup fails (fix keys, `scp`
  the checkpoint back as fallback).
- **VM reboot:** systemd restarts the loop; checkpoint is on persistent disk.
- **GDrive slice skipped:** `RCLONE_GDRIVE_CONFIG` unset or `rclone` missing —
  by design, Supabase + R2 still sync.

## Cost note

Always Free ARM (4 OCPU/24 GB, 10 TB egress) covers this comfortably. Only
metered risk is R2 Class-A writes (photos) — same as Mac runs today.
