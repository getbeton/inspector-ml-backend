# Telegram Analytics

Agent experiments for extracting behavioral insights from Telegram chat data.

## Versions

- `v0.0.1` is the current tracked implementation.

## Current Default

```bash
adk api_server --host 0.0.0.0 --port 8000 /home/scarlet/upsale-agent/projects/telegram_analytics/versions/v0.0.1
```

## Architecture

- Bootstrap tools discover the available chat schema.
- The main loop proposes candidate insights and validates them with read-only SQL.
- Reviewer logic keeps promoted insights narrow and evidence-backed.

## Common Failure Modes

- Chat schema is incomplete or poorly named.
- The target chat does not have enough activity for robust thresholds.
- Database credentials are present but point at the wrong dataset.
