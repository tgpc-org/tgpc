.PHONY: scrape sync quota help dg-start dg-stop dg-resume dg-watch

scrape:  # Scrape TGPC → sync all destinations → enrich new records
	python3 -m tgpc update

sync:  # Full manual sync of rph.json to all cloud destinations
	python3 -m tgpc sync

quota:  # Show free quota usage for all services
	python3 -m tgpc quota

help:  # List all commands
	@grep -E '^[a-z-]+:  #' $(MAKEFILE_LIST) | sed 's/:  #/ — /'

# DG contact collection: the VM does the fetching.
DG_VM ?= $(shell cat data/dg_vm.conf 2>/dev/null)

dg-start:  # Start fetching (preflight + guards + loop)
	DG_VM=$(DG_VM) ./scripts/dg_run.sh --retry-terminal

dg-stop:  # Stop the fetch immediately (sets halt flag)
	./scripts/vps_ctl.sh stop "$(MSG)"

dg-resume:  # Unpause the loop
	./scripts/vps_ctl.sh resume

dg-watch:  # Stream the fetch log live (Ctrl+C stops viewing only)
	gcloud compute ssh tgpc-dg-fetch --zone=asia-south1-c --tunnel-through-iap \
		--command='sudo -u tgpc-mac tail -f /home/tgpc-mac/tgpc/data/dg_fetch.log'
