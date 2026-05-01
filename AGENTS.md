# Repository Guidelines

## Project Structure & Module Organization
- `projects/upsell_ranker/` contains the upsell opportunity system. Versions live under `projects/upsell_ranker/versions/`.
- `projects/upsell_ranker/versions/v0.0.0/` is the earliest tracked baseline. `v0.0.1/` and `v0.0.2/` show later agent iterations.
- `projects/telegram_analytics/` contains a separate analytics system with its own versioned structure.
- `scripts/` holds small local utilities that support exploration but are not runtime agent packages.
- `data/` is for local cached exports only and should stay out of git unless explicitly needed for reproducibility.
- `Dockerfile` runs the packaged upsell agent from `projects/upsell_ranker/versions/v0.0.2`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` creates and activates a local Python env.
- `pip install -r requirements.txt` installs dependencies for ADK tooling and local analysis scripts.
- `adk api_server --host 0.0.0.0 --port 8000 /home/user/upsale-agent/projects/upsell_ranker/versions/v0.0.2` starts the current default upsell agent.
- `adk api_server --host 0.0.0.0 --port 8000 /home/user/upsale-agent/projects/telegram_analytics/versions/v0.0.1` starts the Telegram analytics agent.
- `python -c "import sys; sys.path.append('/home/user/upsale-agent/projects/upsell_ranker/versions/v0.0.0'); from scoring import rank_accounts; print(rank_accounts(top_n=5))"` performs a quick baseline scoring check.

## Coding Style & Naming Conventions
- Python, 4-space indentation, `snake_case` for functions and variables, `PascalCase` for classes.
- Keep agent definitions narrow. Put external integration logic into tool or connector modules.
- Prefer version-local utilities over cross-version coupling unless the shared module is intentionally stable.

## Testing Guidelines
- No broad automated suite is required for day-to-day iteration.
- Prefer smoke checks, import checks, and a few focused tests around fragile parsing or SQL-policy logic.
- Put any committed tests under `tests/` at the repo root.

## Commit & Pull Request Guidelines
- Use Conventional Commits with scopes, for example `feat(upsell-ranker): restructure version layout`.
- In PRs, state which project and version changed and note any path, env, or schema impacts.

## Configuration & Secrets
- Upsell ranking against Attio uses `ATTIO_TOKEN` and respects `ATTIO_LIMIT`, `ATTIO_MAX_PAGES`, and `ATTIO_CACHE_TTL_SEC`.
- Inspector-backed flows use `INSPECTOR_URL`, `AGENT_SECRET`, and `VERCEL_PROTECTION`.
- Never commit `.env` files, local caches, generated reports, or refreshed local data dumps unless that is the explicit task.
