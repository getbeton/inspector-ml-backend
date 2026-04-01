# Telegram Analytics v0.0.1

First tracked version of the Telegram chat analytics system.

## Capabilities

- Discovers likely message, user, reaction, and membership tables.
- Builds a database profile for a target chat.
- Runs a constrained insight loop to promote evidence-backed behavioral findings.

## Architecture Notes

- `chat_analytic/agent.py` defines the staged bootstrap, exploration, review, and finalization flow.
- `chat_analytic/tools.py` owns DB access, SQL policy checks, caching, and artifact persistence.
- The system is intentionally read-only and optimized for investigation, not ETL.

## Run

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/telegram_analytics/versions/v0.0.1
```

## Required Environment

```bash
TELEGRAM_DB_HOST=127.0.0.1
TELEGRAM_DB_PORT=5432
TELEGRAM_DB_NAME=
TELEGRAM_DB_USER=
TELEGRAM_DB_PASSWORD=
```

## Limits

- Assumes PostgreSQL access.
- Threshold inference works best on active chats with enough history.
