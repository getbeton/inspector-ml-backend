# Agent Systems Workspace

Experimental workspace for agentic analytics systems focused on signal discovery, warehouse reasoning, and evidence-backed analysis workflows.

```mermaid
flowchart LR
    UserPrompt[User prompt] --> ProjectSelect{Project}
    ProjectSelect --> Upsell[Upsell Ranker]
    ProjectSelect --> Telegram[Telegram Analytics]
    Upsell --> UpsellFlow[Website or account context -> Inspector-backed warehouse analysis -> signal search]
    Telegram --> TelegramFlow[Chat target -> schema bootstrap -> SQL-backed insight loop]
    UpsellFlow --> Outputs[Reports, signals, ranked opportunities]
    TelegramFlow --> Outputs
    Scripts[Optional local scripts] -. probes .-> Outputs
```

The repo keeps multiple agent lineages side by side so architectural changes stay explicit. The latest versions of both projects are optimized for iterative analysis and are currently most reliable when deployed with Beton Inspector in front of protected systems. A standalone mode that connects directly to systems such as PostHog, Attio, and similar integrations is planned but not implemented yet.

## Projects

- `projects/upsell_ranker` contains the upsell opportunity lineage.
- `projects/telegram_analytics` contains the Telegram chat analytics lineage.
- `scripts/` contains optional local utilities for one-off probing rather than the main agent runtime.

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

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with only the variables needed for the version you want to run. `.env.example` contains placeholders only and should not be treated as working credentials.

## How To Test

Use `adk web` as the primary local workflow. It starts the ADK server with a web UI, which is the intended path for interactive testing, prompt iteration, and debugging.

Upsell ranker:

```bash
adk web /home/user/upsale-agent/projects/upsell_ranker/versions/v0.0.2
```

Telegram analytics:

```bash
adk web /home/user/upsale-agent/projects/telegram_analytics/versions/v0.0.1
```

Keep `adk api_server` for production-style or external API integration scenarios:

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/user/upsale-agent/projects/upsell_ranker/versions/v0.0.2
adk api_server --host 0.0.0.0 --port 8000 /home/user/upsale-agent/projects/telegram_analytics/versions/v0.0.1
```

Legacy baseline smoke check:

```bash
python -c "import sys; sys.path.append('/home/user/upsale-agent/projects/upsell_ranker/versions/v0.0.0'); from scoring import rank_accounts; print(rank_accounts(top_n=5))"
```

## Environment

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

## Open-Source Notes

- This repository is licensed under AGPLv3. See `LICENSE`.
- Beton Inspector is currently the security-focused deployment path for protected warehouse access in the newest agent versions.
- Direct standalone integration flags for systems such as PostHog and Attio are planned.
- Recent experiments show that explicitly defining the target metric for an agent is the strongest lever on analysis quality.

## Notes

- Generated artifacts, caches, logs, and local report outputs are intentionally ignored.
- Version-specific details live in each project and version README.
- Public docs use "warehouse" in place of internal shorthand such as "DWH" on first mention.
