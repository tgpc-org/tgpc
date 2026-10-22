.PHONY: scrape sync quota logs help dg-start dg-stop dg-resume dg-watch clean-data clean-ui clean

# Use the repo venv when present (survives Homebrew Python upgrades).
PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

# Every long run keeps its full output under data/logs/ (gitignored), so two
# runs can be diffed later instead of reconstructed from terminal scrollback.
#
# /bin/sh is dash on the VM, where `set -o pipefail` does not exist, so the
# recipes run under bash. pipefail matters: a bare `cmd | tee` reports tee's
# status, so a crashed scrape would look successful and the log would hide it.
SHELL := /bin/bash

LOG_DIR := data/logs
KEEP_LOGS ?= 30

# $(1) = log prefix, $(2) = command to run. Only the newest $(KEEP_LOGS) logs
# per prefix are kept, so this cannot grow without bound. The PID in the name
# stops two runs in the same second from overwriting each other.
define RUN_LOGGED
mkdir -p $(LOG_DIR); \
log="$(LOG_DIR)/$(1)-$$(date +%Y%m%d-%H%M%S)-$$$$.log"; \
echo "log → $$log"; \
set -o pipefail; \
{ $(2) 2>&1 | tee "$$log"; }; status=$$?; \
ln -sfn "$$(basename "$$log")" "$(LOG_DIR)/latest-$(1).log"; \
ls -1t $(LOG_DIR)/$(1)-*.log 2>/dev/null | tail -n +$$(( $(KEEP_LOGS) + 1 )) | while read -r f; do rm -f "$$f"; done; \
exit $$status
endef

scrape:  # Scrape TGPC → sync all destinations → enrich new records
	@$(call RUN_LOGGED,scrape,$(PY) -m tgpc update)

sync:  # Full manual sync of rph.json to all cloud destinations
	@$(call RUN_LOGGED,sync,$(PY) -m tgpc sync)

logs:  # Show the newest scrape log (FOLLOW=1 to tail it)
	@last=$$(ls -1t $(LOG_DIR)/scrape-*.log 2>/dev/null | head -1); \
	if [ -z "$$last" ]; then echo "no run logs yet — run 'make scrape'"; exit 1; fi; \
	echo "== $$last =="; \
	if [ -n "$(FOLLOW)" ]; then tail -f "$$last"; else tail -n 60 "$$last"; fi

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
