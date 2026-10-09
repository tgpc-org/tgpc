.PHONY: scrape sync quota help dg-status dg-watch dg-start dg-halt dg-resume dg-vm dg-ssh dg-open

help:  # List all commands
	@grep -E '^[a-z-]+:  #' $(MAKEFILE_LIST) | sed 's/:  #/ — /'

scrape:  # Scrape TGPC → sync all destinations → enrich new records
	python3 -m tgpc update

sync:  # Full manual sync of rph.json to all cloud destinations
	python3 -m tgpc sync

quota:  # Show free quota usage for all services
	python3 -m tgpc quota

# --- DG contact collection (VM does the fetching; these are the only
# commands you need). VM identity defaults to the saved static setup;
# override with `make dg-start DG_VM=user@host DG_ZONE=region-zone`.
DG_VM ?= $(shell cat data/dg_vm.conf 2>/dev/null)
DG_ZONE ?= asia-south1-c
DG_NAME ?= tgpc-dg-fetch

dg-status:  # Scoreboard: saved / refused / to-try + loop heartbeat
	python3 -c "import json;cp=json.load(open('data/dg_fetch_checkpoint.json'));c=len(cp.get('completed',[]));t=len(cp.get('failed_terminal',{}));print(f'saved {c} refused {t} to-try {90356-c-t}')"
	./scripts/vps_ctl.sh status

dg-watch:  # Stream the VM fetch log live (Ctrl+C stops viewing only)
	gcloud compute ssh $(DG_NAME) --zone=$(DG_ZONE) --tunnel-through-iap \
		--command='sudo -u tgpc-mac tail -f /home/tgpc-mac/tgpc/data/dg_fetch.log'

dg-start:  # Full start: preflight + drift guards + smoke + loop (refuses loudly on problems)
	DG_VM=$(DG_VM) ./scripts/dg_run.sh --retry-terminal

dg-halt:  # Pause after the current batch: make dg-halt MSG="reason here"
	./scripts/vps_ctl.sh halt "$(MSG)"

dg-resume:  # Unpause the loop
	./scripts/vps_ctl.sh resume

dg-vm:  # Is the VM awake, and on which IP?
	gcloud compute instances describe $(DG_NAME) --zone=$(DG_ZONE) \
		--format='value(status,networkInterfaces[0].accessConfigs[0].natIP)'

dg-ssh:  # Open a shell on the VM
	gcloud compute ssh $(DG_NAME) --zone=$(DG_ZONE) --tunnel-through-iap

dg-open:  # Local dashboard in a browser (same controls, no login)
	./scripts/dg_open.sh
