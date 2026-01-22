import json
import os
from typing import Any, Dict

import requests

from shared.cache import cache_read_json, cache_write_json

_QUERY_COUNT = 0


def _ph_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _is_safe_readonly(query: str) -> bool:
    lower = query.strip().lower()
    if not (lower.startswith("select") or lower.startswith("with")):
        return False
    if ";" in lower.strip().rstrip(";"):
        return False
    forbidden = [
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "truncate",
        "attach",
        "detach",
        "optimize",
    ]
    return not any(word in lower for word in forbidden)


def _enforce_limit(query: str, max_rows: int) -> str:
    lower = query.lower()
    if " limit " in lower:
        return query
    return f"{query.rstrip()} LIMIT {max_rows}"


def run_posthog_query(hogql: str) -> Dict[str, Any]:
    """
    Read-only HogQL executor with strict safety and query budget limits.
    """
    global _QUERY_COUNT
    max_queries = int(os.getenv("SIGNAL_AGENT_MAX_QUERIES", "6"))
    max_rows = int(os.getenv("SIGNAL_AGENT_MAX_ROWS", "200"))
    token = os.getenv("POSTHOG_PERSONAL_API_KEY", "").strip()
    host = os.getenv("POSTHOG_HOST", "https://us.posthog.com").strip().rstrip("/")
    project_id = os.getenv("POSTHOG_PROJECT_ID", "").strip()

    if not token or not project_id:
        return {
            "ok": False,
            "error": "missing_posthog_credentials",
            "details": {"host": host, "project_id": project_id},
        }

    if _QUERY_COUNT >= max_queries:
        return {"ok": False, "error": "query_budget_exceeded", "max_queries": max_queries}

    if not _is_safe_readonly(hogql):
        return {"ok": False, "error": "unsafe_query_rejected"}

    _QUERY_COUNT += 1
    safe_query = _enforce_limit(hogql, max_rows)
    cache_key = f"posthog_signal_query:v1:project_id={project_id}:query={safe_query}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return {
            "ok": True,
            "cached": True,
            "query": safe_query,
            "columns": cached.get("columns"),
            "rows": cached.get("results", []),
        }

    url = f"{host}/api/projects/{project_id}/query/"
    payload = {"query": {"kind": "HogQLQuery", "query": safe_query}}
    resp = requests.post(url, headers=_ph_headers(token), data=json.dumps(payload), timeout=180)
    resp.raise_for_status()
    data = resp.json()
    cache_write_json(cache_key, data)
    return {
        "ok": True,
        "cached": False,
        "query": safe_query,
        "columns": data.get("columns"),
        "rows": data.get("results", []),
    }
