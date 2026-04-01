# Upsell Ranker

Agent experiments for finding and explaining expansion opportunities.

## Versions

- `v0.0.0` is the baseline single-agent prototype over Attio exports.
- `v0.0.1` expands the workflow to a broader upsell dataset with external connectors.
- `v0.0.2` splits the system into focused agents for company understanding, DWH exploration, and signal discovery.

## Current Default

`versions/v0.0.2`

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/upsell_ranker/versions/v0.0.2
```

## Architecture

- `upsell_agent` turns a website or account context into an initial operating hypothesis.
- `dwh_analyst` maps available warehouse tables, joins, and likely metrics.
- `signal_agent` runs a guarded loop that proposes and validates reusable signals.
- `shared/` contains cache and Inspector client helpers reused inside `v0.0.2`.

## Common Failure Modes

- Missing integration secrets: the agents degrade, but outputs become more assumption-heavy.
- Weak warehouse metadata: join discovery falls back to sampled overlap and can stay ambiguous.
- Noisy event schemas: `signal_agent` may spend its query budget validating weak hypotheses instead of promoting signals.
