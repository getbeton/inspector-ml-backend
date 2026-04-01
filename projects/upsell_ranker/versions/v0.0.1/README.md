# Upsell Ranker v0.0.1

Second iteration of the upsell system.

## Increment From v0.0.0

- Broader dataset assembly instead of ranking only raw Attio exports.
- Added connectors and enrichment-oriented tooling.
- Produces a more sales-ready report with coverage notes and next steps.

## Architecture Notes

- Still centered on one top-level upsell agent.
- External systems are pulled in through tool modules rather than separate specialist agents.
- This is the bridge between the simple baseline and the modular `v0.0.2` layout.

## Run

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/upsell_ranker/versions/v0.0.1
```

## Limits

- Integration availability changes output quality a lot.
- Prompt and output format are still tightly coupled inside one agent.
