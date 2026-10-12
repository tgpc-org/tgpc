.PHONY: scrape sync quota help dg-start dg-stop dg-resume dg-watch

# Use the repo venv when present (survives Homebrew Python upgrades).
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

scrape:  # Scrape TGPC → sync all destinations → enrich new records
	$(PY) -m tgpc update

sync:  # Full manual sync of rph.json to all cloud destinations
	$(PY) -m tgpc sync

quota:  # Show free quota usage for all services
	$(PY) -m tgpc quota

help:  # List all commands
	@grep -E '^[a-z-]+:  #' $(MAKEFILE_LIST) | sed 's/:  #/ — /'

# DG contact collection: the VM does the fetching.
DG_VM ?= $(shell cat data/dg_vm.conf 2>/dev/null)

dg-start:  # Start fetching (stops any running loop first, then starts fresh)
	@echo "Stopping any running fetch loop first..."
	@./scripts/vps_ctl.sh stop "pre-start" 2>/dev/null || true
	@sleep 2
	DG_VM=$(DG_VM) ./scripts/dg_run.sh --retry-terminal --restart

dg-stop:  # Stop the fetch immediately (sets halt flag)
	./scripts/vps_ctl.sh stop "$(MSG)"

dg-resume:  # Unpause the loop
	./scripts/vps_ctl.sh resume

dg-watch:  # Stream the fetch log live (Ctrl+C stops viewing only)
	gcloud compute ssh tgpc-dg-fetch --zone=asia-south1-c --tunnel-through-iap \
		--command='sudo -u tgpc-mac tail -f /home/tgpc-mac/tgpc/data/dg_fetch.log'
