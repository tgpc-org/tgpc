.PHONY: check scrape sync quota logs help dg-start dg-stop dg-resume dg-watch clean-data clean-ui clean

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

# Every gate AGENTS.md lists, in one command. Deliberately NOT chained with
# `&&`: the usual reason to run this locally is "what exactly did I break?",
# and stopping at the first failure would hide the other seven. Each gate
# prints its own ok/FAIL line, a failure shows the tail of its output, and the
# target exits non-zero if any of them failed. The two dependency audits
# (`npm audit`, `pip-audit`) stay in CI — they need the network and only fail
# once an advisory exists.
check:  # Run every quality gate (UI + Python); exits non-zero if any gate fails
	@if [ ! -d ui/node_modules ]; then \
		echo "check: ui/node_modules is missing — run 'cd ui && npm ci' first"; exit 1; \
	fi
	@if [ ! -x .venv/bin/python ]; then \
		echo "check: .venv is missing — run:"; \
		echo "  python3 -m venv .venv && .venv/bin/pip install -e . ruff mypy pytest pytest-cov"; \
		exit 1; \
	fi
	@start=$$(date +%s); failed=""; \
	note=""; \
	pinned() { grep -oE "$$1==[0-9.]+" .github/workflows/python.yml | head -1 | cut -d= -f3; }; \
	drift() { \
		want=$$(pinned "$$1"); \
		have=$$(eval "$$2 $$3" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1); \
		if [ -n "$$want" ] && [ -n "$$have" ] && [ "$$want" != "$$have" ]; then \
			note="$$note  note: local $$1 $$have != the CI pin $$want in .github/workflows/python.yml\n"; \
		fi; \
	}; \
	drift ruff "$(PY)" "-m ruff --version"; \
	drift mypy "$(PY)" "-m mypy --version"; \
	run() { \
		name="$$1"; dir="$$2"; cmd="$$3"; \
		printf '  %-20s' "$$name"; \
		if out=$$(cd "$$dir" && eval "$$cmd" 2>&1); then \
			printf 'ok\n'; \
		else \
			printf 'FAIL\n'; \
			printf '%s\n' "$$out" | tail -n 30 | sed 's/^/      /'; \
			failed="$$failed $$name"; \
		fi; \
	}; \
	echo "tgpc quality gates"; \
	printf "$$note"; \
	run "ui svelte-check"  ui "npm run check"; \
	run "ui eslint"        ui "npm run lint"; \
	run "ui brand colors"  ui "npm run check:colors"; \
	run "ui unit tests"    ui "npm run test:unit"; \
	run "ui e2e typecheck" ui "npm run check:e2e"; \
	run "py ruff"          .  "$(PY) -m ruff check . && $(PY) -m ruff format --check ."; \
	run "py mypy"          .  "$(PY) -m mypy"; \
	run "py pytest"        .  "$(PY) -m pytest tests/ -q --cov --cov-fail-under=75"; \
	if [ -n "$$failed" ]; then \
		echo; echo "FAILED:$$failed"; exit 1; \
	fi; \
	echo; echo "all 8 gates passed in $$(( $$(date +%s) - start ))s"

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
