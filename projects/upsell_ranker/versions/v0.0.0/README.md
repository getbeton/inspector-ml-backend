# Upsell Ranker v0.0.0

First tracked baseline for the upsell work.

## What Changed

- Single ADK agent over cached Attio data.
- Simple ranking flow with reasons and next actions.
- No multi-agent coordination and no warehouse exploration.

## Why It Exists

This version is the smallest usable baseline. It keeps the original scoring idea visible before the later multi-agent splits.

## Run

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/upsell_ranker/versions/v0.0.0
```

## Limits

- Depends on Attio-shaped data.
- Scoring is intentionally heuristic.
- Good for sanity checks, not deep investigation.
