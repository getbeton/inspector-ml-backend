# Mason eval scaffold — make targets.
#
# Source credentials FIRST (Langfuse + PostHog + model keys):
#   source ~/.claude/secrets/mason-flash3.env
#   export ANTHROPIC_API_KEY=...      # opus-baseline profile
#   export GEMINI_API_KEY=...         # gemini-3-flash profile  (or GOOGLE_API_KEY)
#
# See scripts/evals/README.md for the full runbook.

PYTHON      ?= python3
EVALS       := scripts/evals
SCENARIO    ?= posthog_seat_based_mvp
PROFILE     ?= gemini-3-flash
EVAL_SET    ?= $(EVALS)/golden/seat_based_smoke.evalset.json
ARTIFACTS   := artifacts/evals

.PHONY: help eval-dataset eval-dataset-offline eval-run eval-compare eval-clean

help:
	@echo "Mason eval targets:"
	@echo "  make eval-dataset           dryfit -> Langfuse dataset (SCENARIO=$(SCENARIO))"
	@echo "  make eval-dataset-offline   build dataset items from a prior dryfit run, no upload"
	@echo "  make eval-run               ADK 11-criteria eval, one profile (PROFILE=$(PROFILE))"
	@echo "  make eval-compare           run EVAL_SET across profiles, emit comparison table"
	@echo "  make eval-clean             remove $(ARTIFACTS)"

# (a) dryfit -> Langfuse dataset. Needs DRYFIT_DIR + Langfuse keys (+ PostHog
#     keys unless DRY_RUN=1). Generates fresh ground truth then uploads.
eval-dataset:
	$(PYTHON) $(EVALS)/eval_dataset.py --generate --scenario $(SCENARIO) $(if $(DRY_RUN),--dry-run,)

# offline variant: reuse an existing dryfit output dir, build items, no upload.
eval-dataset-offline:
	$(PYTHON) $(EVALS)/eval_dataset.py --scenario $(SCENARIO) --no-upload \
		--json-out $(ARTIFACTS)/dataset_items_$(SCENARIO).json

# (b) ADK eval against the golden set for a single model profile.
eval-run:
	$(PYTHON) $(EVALS)/eval_run.py --profile $(PROFILE) --eval-set $(EVAL_SET) \
		--json-out $(ARTIFACTS)/run_$(PROFILE).json

# (c) same eval set across 2+ profiles -> comparison table.
eval-compare:
	$(PYTHON) $(EVALS)/eval_compare.py --eval-set $(EVAL_SET) --out-dir $(ARTIFACTS)

eval-clean:
	rm -rf $(ARTIFACTS)
