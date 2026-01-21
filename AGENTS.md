# Repository Guidelines

## Project Structure & Module Organization
- `upsell_ranker_full/` is the primary workspace. Each version folder (for example, `upsell_ranker_full/v0.0.1/`) represents a distinct agent architecture or feature set.
- `upsell_ranker_full/v0.0.2/` will host the next active Google ADK agent iteration.
- `upsell_ranker/` and `data_analysis/` are exploratory or first-pass implementations and manual data exploration tools; treat them as references, not the main development target.
- `data/` contains cached Attio exports (`companies.jsonl`, `deals.jsonl`) used for local ranking runs.
- `Dockerfile` runs the packaged agent from `upsell_ranker_full`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` creates and activates a local Python env.
- `pip install -r requirements.txt` installs dependencies for ADK tooling and data workflows.
- `adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/upsell_ranker_full/v0.0.1` starts the current packaged agent (update path for `v0.0.2` when created).
- `python -c "import sys; sys.path.append('/home/scarlet/upsale-agent/upsell_ranker_full/v0.0.1'); from upsell_agent.scoring import rank_accounts; print(rank_accounts(top_n=5))"` performs a quick scoring sanity check.

## Coding Style & Naming Conventions
- Python, 4-space indentation, `snake_case` for functions/vars, `PascalCase` for classes.
- Keep ADK agent definitions focused; route external integrations through tools modules.
- New MCP tools (PostHog access, time-series analysis) should live under the active versioned agent directory and be explicitly wired into the ADK agent.

## Testing Guidelines
- No automated test suite is configured.
- If you add tests, put them under `tests/` at the repo root and document the command here.

## Commit & Pull Request Guidelines
- Commits follow Conventional Commit style with scopes, e.g. `feat(railway): add files necessary for railway deployment`.
- PRs should explain which versioned agent directory changed and outline any data/schema impacts.

## Configuration & Secrets
- Attio access requires `ATTIO_TOKEN` and respects `ATTIO_LIMIT`, `ATTIO_MAX_PAGES`, and `ATTIO_CACHE_TTL_SEC`.
- Avoid committing refreshed `data/*.jsonl` unless explicitly requested for reproducibility.
