# Contributing

## Scope

This repository contains experimental agent systems with versioned project layouts. Keep changes narrow, document behavioral shifts, and preserve older versions as historical baselines unless the task is explicitly to refactor them.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Use `adk web` for local testing and debugging. Keep `adk api_server` for production-style serving checks.

## Expectations

- Follow the versioned structure under `projects/`.
- Prefer focused tests, smoke checks, and import checks over broad rewrites.
- Update the relevant README when behavior, architecture, env vars, or failure modes change.
- Never commit real credentials, customer data, or local `.env` files.
- Keep generated artifacts, caches, and one-off responses out of version control.

## Commits

Use Conventional Commits with a scope when possible, for example `feat(upsell-ranker): improve signal loop docs`.

## Pull Requests

Include:

- which project and version changed
- any environment, path, or schema impact
- how the change was tested
