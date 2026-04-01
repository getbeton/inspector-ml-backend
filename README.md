# Agent Systems Workspace

Small research workspace for agentic analytics systems.

## Projects

- `projects/upsell_ranker` contains the upsell opportunity lineage.
- `projects/telegram_analytics` contains the Telegram chat analytics lineage.
- `scripts/` contains small local utilities that are useful across experiments but are not part of the runtime agents.

## Repository Layout

```text
projects/
  upsell_ranker/
    versions/
      v0.0.0/
      v0.0.1/
      v0.0.2/
  telegram_analytics/
    versions/
      v0.0.1/
scripts/
```

Each project keeps versions side by side to make architectural changes explicit.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with the variables you need for the version you want to run. Most values are optional unless that version uses the corresponding integration.

## Run Upsell Ranker

Current default version:

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/upsell_ranker/versions/v0.0.2
```

Legacy baseline smoke check:

```bash
python -c "import sys; sys.path.append('/home/scarlet/upsale-agent/projects/upsell_ranker/versions/v0.0.0'); from scoring import rank_accounts; print(rank_accounts(top_n=5))"
```

## Run Telegram Analytics

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/telegram_analytics/versions/v0.0.1
```

## Environment

Copy `.env.example` and keep only the values relevant to your run.

Core variables used in the repo:

```bash
GOOGLE_API_KEY=
AGENTOPS_API_KEY=

ATTIO_TOKEN=
ATTIO_LIMIT=200
ATTIO_MAX_PAGES=10
ATTIO_CACHE_TTL_SEC=21600

INSPECTOR_URL=
AGENT_SECRET=
VERCEL_PROTECTION=

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

TELEGRAM_ANALYTICS_MODEL=gemini-3-flash-preview
TELEGRAM_ANALYTICS_REVIEWER_MODEL=gemini-3-flash-preview
TELEGRAM_DB_HOST=127.0.0.1
TELEGRAM_DB_PORT=5432
TELEGRAM_DB_NAME=
TELEGRAM_DB_USER=
TELEGRAM_DB_PASSWORD=
```

## Notes

- Generated artifacts, caches, logs, and local report outputs are intentionally ignored.
- Version-specific details live in each version README.
