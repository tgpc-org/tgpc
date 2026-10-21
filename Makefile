.PHONY: scrape sync quota help dg-start dg-stop dg-resume dg-watch clean-data clean-ui clean

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

dg-start:  # Start fetching (stops any running fetch first, then starts fresh)
	@echo "Stopping any running fetch first..."
	@./scripts/vps_ctl.sh stop "pre-start" 2>/dev/null || true
	@sleep 2
	DG_VM=$(DG_VM) ./scripts/dg_run.sh --retry-terminal --restart

dg-stop:  # Stop the run after the current record (nothing lost)
	./scripts/vps_ctl.sh stop "$(MSG)"

dg-resume:  # Unpause the run
	./scripts/vps_ctl.sh resume

dg-watch:  # Stream the fetch log live (Ctrl+C stops viewing only)
	gcloud compute ssh tgpc-dg-fetch --zone=asia-south1-c --tunnel-through-iap \
		--command='sudo -u tgpc-mac tail -f /home/tgpc-mac/tgpc/data/dg_fetch.log'

# Housekeeping. Deliberately conservative: NEVER removes rph.json, backups/,
# dg_contacts.jsonl (captcha-gated — expensive to re-fetch) or any *.jsonl
# dataset. Only regenerable scratch and build output.
clean-data:  # Remove regenerable scratch under data/ (keeps every dataset)
	rm -rf data/webp data/img data/progress data/backups/*.restore_tmp
	rm -f data/dg_captcha_tmp.jpg data/*.tmp data/*.restore_tmp
	rm -f data/dg_fetch_checkpoint.json.bak-*
	@echo "kept: rph.json, backups/, dg_contacts.jsonl, *.jsonl"

clean-ui:  # Remove UI build/cache/test artifacts
	rm -rf ui/.svelte-kit ui/.wrangler ui/build ui/dist ui/test-results ui/playwright-report

clean: clean-data clean-ui  # Both of the above
