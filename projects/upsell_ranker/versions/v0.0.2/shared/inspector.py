"""Inspector callback helpers (with optional direct-PostHog bypass).

In normal operation Mason's tools call back to Inspector's `/api/agent/*`
endpoints, which proxy to PostHog/Postgres/etc. on Mason's behalf. For the
test rig — and only the test rig — we support a direct-PostHog bypass
selected by the `MASON_DIRECT_POSTHOG=1` env var. When set, the helpers
translate the Inspector path to an equivalent PostHog Personal-API-Key call
and shape the response to match what Mason expects.

Bypass coverage:
  GET  /api/agent/list-tables       → fixed list ['events', 'persons']
                                      (PostHog has a fixed event schema; no
                                      data-warehouse tables in this rig).
  GET  /api/agent/list-columns      → HogQL `SELECT * FROM events LIMIT 0`
                                      column probe; falls back to a known
                                      list when PostHog can't introspect.
  POST /api/agent/sql-proxy         → POST {host}/api/projects/{pid}/query/
                                      with HogQLQuery wrapper.
  POST /api/agent/data/website-exploration → write to local artifacts file.
  POST /api/agent/signals           → write to local artifacts file.

Anything else falls through to the real Inspector. The bypass is OFF by
default; production code paths are unchanged when MASON_DIRECT_POSTHOG≠1.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


def inspector_env() -> Dict[str, str]:
    return {
        "url": os.getenv("INSPECTOR_URL", "https://staging.getbeton.org").strip().rstrip("/"),
        "agent_secret": (
            os.getenv("INSPECTOR_AGENT_SECRET", "")
            or os.getenv("AGENT_SECRET", "")
        ).strip(),
        "vercel_protection": (
            os.getenv("INSPECTOR_VERCEL_PROTECTION", "")
            or os.getenv("VERCEL_PROTECTION", "")
        ).strip(),
    }


def inspector_headers(
    agent_secret: str,
    vercel_protection: str = "",
    include_vercel: bool = True,
) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "x-agent-secret": agent_secret,
    }
    if include_vercel and vercel_protection:
        headers["x-vercel-protection-bypass"] = vercel_protection
    return headers


# ── Direct-PostHog bypass ────────────────────────────────────────────


def _direct_posthog_enabled() -> bool:
    return os.getenv("MASON_DIRECT_POSTHOG", "").strip().lower() in ("1", "true", "yes", "on")


def _ph_env() -> Dict[str, str]:
    return {
        "host": (os.getenv("POSTHOG_HOST") or "https://us.posthog.com").rstrip("/"),
        "personal_api_key": (os.getenv("POSTHOG_PERSONAL_API_KEY") or "").strip(),
        "project_id": (os.getenv("POSTHOG_PROJECT_ID") or "").strip(),
    }


def _ph_query(hogql: str) -> Dict[str, Any]:
    env = _ph_env()
    if not (env["personal_api_key"] and env["project_id"]):
        raise RuntimeError("MASON_DIRECT_POSTHOG=1 but POSTHOG_PERSONAL_API_KEY / POSTHOG_PROJECT_ID not set")
    url = f"{env['host']}/api/projects/{env['project_id']}/query/"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {env['personal_api_key']}",
            "Content-Type": "application/json",
        },
        json={"query": {"kind": "HogQLQuery", "query": hogql}},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _direct_artifacts_dir() -> Path:
    base = os.getenv(
        "MASON_DIRECT_POSTHOG_ARTIFACTS",
        str(Path(__file__).resolve().parents[1] / "artifacts" / "direct_posthog"),
    )
    p = Path(base).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _direct_get(path: str, params: Optional[dict] = None) -> Dict[str, Any]:
    params = params or {}
    if path.endswith("/list-tables"):
        # Mason consumes `tables: [{table_name, source_type}, ...]`.
        names = ["events", "persons"]
        return {
            "tables": [
                {"table_name": n, "source_type": "posthog"}
                for n in names
            ],
        }
    if path.endswith("/list-columns"):
        table = str(params.get("table_id") or "events").strip()
        # Best-effort introspection via a 0-row HogQL probe.
        try:
            data = _ph_query(f"SELECT * FROM {table} LIMIT 0")
            cols = data.get("columns") or data.get("types") or []
            normalized: List[Dict[str, Any]] = []
            for entry in cols:
                if isinstance(entry, dict):
                    normalized.append(entry)
                elif isinstance(entry, (list, tuple)) and len(entry) >= 1:
                    normalized.append({"name": str(entry[0]), "type": str(entry[1]) if len(entry) > 1 else ""})
                else:
                    normalized.append({"name": str(entry)})
            if normalized:
                return {"columns": normalized}
        except Exception:
            pass
        # Fallback: known PostHog event columns.
        if table.lower() == "events":
            return {
                "columns": [
                    {"name": "uuid", "type": "UUID"},
                    {"name": "event", "type": "String"},
                    {"name": "properties", "type": "Object"},
                    {"name": "timestamp", "type": "DateTime"},
                    {"name": "team_id", "type": "Int64"},
                    {"name": "distinct_id", "type": "String"},
                    {"name": "person_id", "type": "UUID"},
                ],
            }
        if table.lower() == "persons":
            return {
                "columns": [
                    {"name": "id", "type": "UUID"},
                    {"name": "properties", "type": "Object"},
                    {"name": "created_at", "type": "DateTime"},
                ],
            }
        return {"columns": []}
    raise RuntimeError(f"direct-PostHog bypass: unsupported GET path {path}")


def _direct_post(path: str, payload: dict) -> Dict[str, Any]:
    if path.endswith("/sql-proxy"):
        query = str(payload.get("query") or "")
        data = _ph_query(query)
        # Mason reads BOTH `types` and `columns` in different code paths.
        # Map PostHog's `columns` → Mason's `types` while also exposing
        # `columns` so newer code paths keep working.
        cols = data.get("columns") or []
        normalized_types: List[Dict[str, Any]] = []
        for entry in cols:
            if isinstance(entry, dict):
                normalized_types.append({"name": entry.get("name", ""), "type": entry.get("type", "")})
            elif isinstance(entry, (list, tuple)) and entry:
                normalized_types.append({"name": str(entry[0]), "type": str(entry[1]) if len(entry) > 1 else ""})
            else:
                normalized_types.append({"name": str(entry), "type": ""})
        return {
            "results": data.get("results") or [],
            "columns": cols,
            "types": normalized_types,
            "query_status": "completed",
            "cached": False,
            "query_id": "",
            "execution_time_ms": int(data.get("timings", [{}])[-1].get("elapsed_ms", 0)) if data.get("timings") else 0,
            "row_count": len(data.get("results") or []),
        }
    if path.endswith("/data/website-exploration"):
        out = _direct_artifacts_dir() / "website_exploration.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {"ok": True, "stored_at": str(out)}
    if path.endswith("/signals") or path.endswith("/agent/signals"):
        out = _direct_artifacts_dir() / f"signals_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {"ok": True, "stored_at": str(out)}
    raise RuntimeError(f"direct-PostHog bypass: unsupported POST path {path}")


# ── Public helpers ──────────────────────────────────────────────────


def inspector_get(
    url: str,
    path: str,
    agent_secret: str,
    vercel_protection: str,
    params: Optional[dict] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    if _direct_posthog_enabled():
        return _direct_get(path, params)
    resp = requests.get(
        f"{url}{path}",
        headers=inspector_headers(agent_secret, vercel_protection, include_vercel=True),
        params=params or {},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def inspector_post(
    url: str,
    path: str,
    agent_secret: str,
    vercel_protection: str,
    payload: dict,
    timeout: int = 60,
    include_vercel: bool = True,
) -> Dict[str, Any]:
    if _direct_posthog_enabled():
        return _direct_post(path, payload)
    resp = requests.post(
        f"{url}{path}",
        headers=inspector_headers(agent_secret, vercel_protection, include_vercel=include_vercel),
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
