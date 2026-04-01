# Upsell Ranker v0.0.2

Current modular iteration of the upsell system.

## Increment From v0.0.1

- Split the flow into specialist agents instead of one broad upsell agent.
- Added warehouse exploration through Inspector-backed tooling.
- Added a guarded signal-discovery loop with SQL policy checks and query budgeting.

## Architecture

Flow:

`upsell_agent -> dwh_analyst -> signal_agent`

- `upsell_agent` reads website context and emits a compact business hypothesis.
- `dwh_analyst` inspects tables, columns, join hints, and likely metric surfaces.
- `signal_agent` explores candidate signals with a reviewer loop and read-only SQL enforcement.
- `shared/` contains cache helpers and the Inspector client used across the version.

## Runtime Requirements

Required for the main warehouse flow:

```bash
INSPECTOR_URL=
AGENT_SECRET=
VERCEL_PROTECTION=
```

Optional tuning:

```bash
UPSELL_AGENT_MODEL=gemini-3-flash-preview
DWH_ANALYST_MODEL=gemini-3-flash-preview
SIGNAL_AGENT_MODEL=gemini-3-flash-preview
SIGNAL_REVIEWER_MODEL=gemini-3-flash-preview

SIGNAL_AGENT_MAX_QUERIES=6
SIGNAL_AGENT_MAX_ROWS=200
SIGNAL_AGENT_MAX_ITERATIONS=8
SIGNAL_AGENT_TARGET_PROMOTED=3
SIGNAL_AGENT_MAX_FAILURES=5
SIGNAL_AGENT_MIN_ITERATIONS_BEFORE_EXIT=3
```

## Run

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/upsell_ranker/versions/v0.0.2
```

Example prompt shape:

```text
Analyze website: 'https://example.com'. Use the Inspector callback URL to query DWH via proxy routes. Your session ID for Inspector callbacks is: sess_456. Your workspace ID is: ws_123. Return JSON only.
```

## Common Problems

- Missing Inspector credentials: website reasoning still works, warehouse stages return unavailable or partial output.
- Sparse metadata samples: join candidates stay tentative.
- Strict SQL policy: some otherwise valid warehouse queries are intentionally rejected to keep the loop bounded and read-only.
