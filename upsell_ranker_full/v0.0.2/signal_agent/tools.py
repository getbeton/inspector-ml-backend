import os
from typing import Any, Dict

from shared.cache import cache_read_json, cache_write_json
from shared.inspector import inspector_env, inspector_post

_QUERY_COUNT = 0


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


def run_posthog_query(hogql: str, session_id: str = "") -> Dict[str, Any]:
    """
    Read-only HogQL executor via Inspector SQL proxy with safety and query budget limits.
    """
    global _QUERY_COUNT
    max_queries = int(os.getenv("SIGNAL_AGENT_MAX_QUERIES", "6"))
    max_rows = int(os.getenv("SIGNAL_AGENT_MAX_ROWS", "200"))
    session_id = session_id.strip()

    if not session_id:
        return {"ok": False, "error": "missing_session_id"}

    env = inspector_env()
    if not env["agent_secret"]:
        return {"ok": False, "error": "missing_inspector_agent_secret"}

    if _QUERY_COUNT >= max_queries:
        return {"ok": False, "error": "query_budget_exceeded", "max_queries": max_queries}

    if not _is_safe_readonly(hogql):
        return {"ok": False, "error": "unsafe_query_rejected"}

    _QUERY_COUNT += 1
    safe_query = _enforce_limit(hogql, max_rows)
    cache_key = f"inspector_signal_query:v1:session_id={session_id}:query={safe_query}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return {
            "ok": True,
            "cached": True,
            "query": safe_query,
            "columns": cached.get("types"),
            "rows": cached.get("results", []),
        }

    payload = {"session_id": session_id, "query": safe_query}
    resp = inspector_post(
        env["url"],
        "/api/agent/sql-proxy",
        env["agent_secret"],
        env["vercel_protection"],
        payload,
        timeout=180,
        include_vercel=True,
    )
    cache_write_json(cache_key, resp)
    return {
        "ok": True,
        "cached": False,
        "query": safe_query,
        "columns": resp.get("types"),
        "rows": resp.get("results", []),
    }
