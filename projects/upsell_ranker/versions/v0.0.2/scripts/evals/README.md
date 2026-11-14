# Mason eval scaffold

Evaluation harness for the `upsell_ranker` (Mason) pipeline. Two complementary
layers:

1. **ADK 11-criteria eval** — replays a small, checked-in golden eval set against
   the agent and scores it with `google.adk.evaluation`. Parameterized by model
   profile so the **same** eval set runs across Gemini-3-Flash vs the Opus
   baseline. Model-vs-model is "run once per profile, then diff" — ADK has no
   native compare mode.
2. **dryfit → Langfuse dataset** — turns the dryfit golden scenarios (e.g.
   `posthog_seat_based_mvp.yaml`) into a Langfuse evaluation dataset so
   warehouse-backed runs are comparable over time and across models.

```
dryfit config ─▶ dryfit_to_posthog.py ─▶ ground_truth.json ─▶ eval_dataset.py ─▶ Langfuse dataset
golden evalset ─▶ eval_run.py (per profile) ─▶ eval_compare.py ─▶ comparison table
```

## Files

| File | Role |
|---|---|
| `golden/seat_based_smoke.evalset.json` | The one checked-in golden eval set (ADK `EvalSet` schema). |
| `golden/test_config.json` | ADK criteria + thresholds (`tool_trajectory_avg_score`, `response_match_score`). |
| `model_profiles.json` | Per-role model env-var overrides per profile (`gemini-3-flash`, `opus-baseline`). |
| `eval_run.py` | `make eval-run` — ADK eval, one profile. |
| `eval_compare.py` | `make eval-compare` — same eval set across profiles → comparison table. |
| `eval_dataset.py` | `make eval-dataset` — dryfit → Langfuse dataset. |

Make targets live in the v0.0.2 root `Makefile`.

## Prerequisites

```bash
# from repo root
pip install -r requirements.txt          # includes google-adk, langfuse, litellm

# credentials (NOT in repo)
source ~/.claude/secrets/mason-flash3.env   # LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST, PostHog Project B keys
export ANTHROPIC_API_KEY=...                 # for the opus-baseline profile
export GEMINI_API_KEY=...                    # for the gemini-3-flash profile (or GOOGLE_API_KEY)

# dryfit checkout (only for eval-dataset)
gh repo clone getbeton/dryfit ~/code/dryfit  # or set DRYFIT_DIR
```

All make targets are run from the v0.0.2 root:
`projects/upsell_ranker/versions/v0.0.2/`.

## (a) Build the Langfuse dataset from dryfit

```bash
make eval-dataset                       # generate fresh + upload (needs DRYFIT_DIR + keys)
make eval-dataset DRY_RUN=1             # generate ground truth but don't POST events to PostHog
make eval-dataset-offline               # reuse a prior dryfit run, build items, no upload
```

- `--generate` runs `dryfit_to_posthog.py` in-memory (no Postgres), producing
  `artifacts/dryfit_e2e/<scenario>/ground_truth.json` + `manifest.json`.
- One Langfuse dataset item is created per planted signal template (the dryfit
  golden cases): `input` describes the warehouse, `expected_output` is the
  ground-truth signal Mason should surface (`should_promote: true`,
  `event_names`, `min_coverage`).
- Dataset name defaults to `mason-dryfit-<scenario>`; item ids are stable
  (`<scenario>:<template_id>`) so re-uploads upsert rather than duplicate.

## (b) Run the ADK eval (one profile)

```bash
make eval-run PROFILE=gemini-3-flash
make eval-run PROFILE=opus-baseline
```

`eval_run.py` applies the profile's per-role model env vars (`SIGNAL_AGENT_MODEL`,
`SIGNAL_REVIEWER_MODEL`, `DWH_ANALYST_MODEL`, `UPSELL_AGENT_MODEL`) **before**
importing the agent, then calls `AgentEvaluator.evaluate`. Result JSON →
`artifacts/evals/run_<profile>.json`.

## (c) Compare across model profiles

```bash
make eval-compare                       # uses default_compare from model_profiles.json
make eval-compare PROFILES="gemini-3-flash opus-baseline"
```

Each profile runs in its own subprocess (a process can only import the agent
once), then results are diffed into `artifacts/evals/comparison.md` + `.json`.

## Extending the golden set

- Add cases to `golden/seat_based_smoke.evalset.json` (ADK `EvalCase` schema:
  `conversation[].user_content` + `final_response` + `intermediate_data.tool_uses`).
- To enable the LLM-judge metrics (`final_response_match_v2`, `safety_v1`), add
  them to `golden/test_config.json` and configure a judge model per the ADK
  evaluation docs — they are intentionally left out of the default thresholds so
  `make eval-run` works without extra judge wiring.
- Warehouse-backed golden cases come from dryfit via `make eval-dataset`; the
  coverage scoring there mirrors `scripts/dryfit_e2e/score.py`.

## What still needs creds / a live run to validate

- `pip install -r requirements.txt` in this checkout (ADK/langfuse/litellm not
  installed here) → then `make eval-run PROFILE=...` end-to-end.
- Model API keys (Anthropic / Gemini) for the actual eval execution.
- Langfuse keys for `make eval-dataset` upload; `DRYFIT_DIR` + PostHog Project B
  keys for fresh generation (or use `make eval-dataset-offline` against a prior
  dryfit run).
```
