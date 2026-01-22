import json
import os
from typing import Any, Dict, List, Optional

import requests

from shared.cache import cache_read_json, cache_write_json


def _ph_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ph_get(host: str, token: str, path: str, params: Optional[dict] = None) -> dict:
    resp = requests.get(
        f"{host}{path}",
        headers=_ph_headers(token),
        params=params or {},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_columns(payload: dict) -> Optional[list]:
    cols_raw = payload.get("columns")
    if isinstance(cols_raw, list) and cols_raw:
        if isinstance(cols_raw[0], dict):
            return [c.get("name") for c in cols_raw]
        if isinstance(cols_raw[0], str):
            return cols_raw

    types_raw = payload.get("types")
    if isinstance(types_raw, list) and types_raw and isinstance(types_raw[0], list):
        return [t[0] for t in types_raw if t]

    return None


def _posthog_hogql_query(host: str, token: str, project_id: str, hogql: str) -> Dict[str, Any]:
    cache_key = f"posthog_hogql:v1:project_id={project_id}:query={hogql}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return {
            "ok": True,
            "cached": True,
            "columns": _extract_columns(cached),
            "rows": cached.get("results", []),
        }

    url = f"{host}/api/projects/{project_id}/query/"
    payload = {"query": {"kind": "HogQLQuery", "query": hogql}}
    resp = requests.post(url, headers=_ph_headers(token), data=json.dumps(payload), timeout=180)
    resp.raise_for_status()
    data = resp.json()
    cache_write_json(cache_key, data)
    return {
        "ok": True,
        "cached": False,
        "columns": _extract_columns(data),
        "rows": data.get("results", []),
    }


def _posthog_warehouse_tables(host: str, token: str, project_id: str) -> Dict[str, Any]:
    cache_key = f"posthog_warehouse_tables:v1:project_id={project_id}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return {"ok": True, "cached": True, "data": cached}

    data = _ph_get(host, token, f"/api/projects/{project_id}/warehouse_tables/")
    cache_write_json(cache_key, data)
    return {"ok": True, "cached": False, "data": data}


def _quote_ident(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def _normalize_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    if text in {"", '""', "null", "None"}:
        return None
    return text


def _normalize_join_value(role: str, value: str) -> Optional[str]:
    text = value.strip()
    if role == "email":
        return text.lower()
    if role == "domain_or_url":
        if text.startswith("http://") or text.startswith("https://"):
            no_proto = text.split("://", 1)[-1]
            host = no_proto.split("/", 1)[0]
            return host.lower()
        return text.lower()
    if role == "company_ref" and text.startswith("companies:"):
        return text.split("companies:", 1)[-1]
    if role == "user_ref" and text.startswith("people:"):
        return text.split("people:", 1)[-1]
    return text


def _infer_column_roles(column: str, samples: List[str]) -> List[str]:
    lower = column.lower()
    roles = []
    if "email" in lower:
        roles.append("email")
    if "domain" in lower or "domains" in lower or lower.endswith("url") or "website" in lower:
        roles.append("domain_or_url")
    if "company" in lower or "account" in lower or "org" in lower:
        roles.append("company_ref")
    if "record_id" in lower or lower.endswith("_id") or lower == "id":
        roles.append("id")
    if "stripe" in lower or "customer" in lower or "invoice" in lower:
        roles.append("billing_ref")
    if "user" in lower or "person" in lower:
        roles.append("user_ref")

    for value in samples:
        if "@" in value and "email" not in roles:
            roles.append("email")
        if value.startswith("companies:") and "company_ref" not in roles:
            roles.append("company_ref")
        if value.startswith("people:") and "user_ref" not in roles:
            roles.append("user_ref")
    return roles


def _summarize_sample(columns: List[str], rows: List[list]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"columns": {}}
    max_examples = 3
    for idx, col in enumerate(columns):
        values = []
        for row in rows:
            if idx >= len(row):
                continue
            value = _normalize_value(row[idx])
            if value is not None:
                values.append(value)
        unique_samples = []
        for value in values:
            if value not in unique_samples:
                unique_samples.append(value)
            if len(unique_samples) >= max_examples:
                break
        if not unique_samples and rows:
            unique_samples = ["<empty>"]
        summary["columns"][col] = {
            "non_empty": len(values),
            "empty": max(0, len(rows) - len(values)),
            "samples": unique_samples,
            "roles": _infer_column_roles(col, unique_samples),
        }
    return summary


def posthog_dwh_eda(
    project_id: str = "",
    max_tables: int = 40,
    sample_rows: int = 5,
    sample_columns: int = 8,
) -> Dict[str, Any]:
    """
    PostHog DWH EDA using warehouse tables metadata + targeted samples.
    """
    token = os.getenv("POSTHOG_PERSONAL_API_KEY", "").strip()
    host = os.getenv("POSTHOG_HOST", "https://us.posthog.com").strip().rstrip("/")

    if not token:
        return {
            "status": "unavailable",
            "reason": "POSTHOG_PERSONAL_API_KEY missing",
            "host": host,
            "project_id": project_id or os.getenv("POSTHOG_PROJECT_ID", "").strip(),
        }

    project_id = (project_id or os.getenv("POSTHOG_PROJECT_ID", "")).strip()
    projects = None
    if not project_id:
        org = _ph_get(host, token, "/api/organizations/@current")
        org_id = org.get("id")
        projects = _ph_get(host, token, f"/api/organizations/{org_id}/projects/", params={"limit": 100})
        results = projects.get("results", []) if isinstance(projects, dict) else []
        if results:
            project_id = str(results[0].get("id"))

    if not project_id:
        return {
            "status": "error",
            "reason": "POSTHOG_PROJECT_ID not found",
            "host": host,
            "projects": projects,
        }

    warehouse = _posthog_warehouse_tables(host, token, project_id)
    if not warehouse.get("ok"):
        return {
            "status": "error",
            "reason": "warehouse_tables_failed",
            "host": host,
            "project_id": project_id,
            "warehouse": warehouse,
        }

    tables_data = warehouse.get("data") or {}
    tables = tables_data.get("results") or []
    tables = tables[: max(1, int(max_tables))]

    table_summaries = []
    join_hints: List[Dict[str, Any]] = []
    join_candidates: List[Dict[str, Any]] = []

    for table in tables:
        name = table.get("name")
        columns = [c.get("name") for c in (table.get("columns") or []) if c.get("name")]
        table_summary: Dict[str, Any] = {
            "name": name,
            "format": table.get("format"),
            "column_count": len(columns),
            "columns": columns,
        }

        if not name:
            table_summary["sample_status"] = "skipped_missing_name"
            table_summaries.append(table_summary)
            continue

        pick_cols = columns[: max(1, int(sample_columns))] if columns else []
        select_cols = ", ".join(_quote_ident(col) for col in pick_cols) if pick_cols else "*"
        hogql = f"SELECT {select_cols} FROM {_quote_ident(name)} LIMIT {int(sample_rows)}"
        try:
            sample = _posthog_hogql_query(host, token, project_id, hogql)
            rows = sample.get("rows") or []
            summary = _summarize_sample(pick_cols or columns, rows)
            table_summary["sample_status"] = "ok"
            table_summary["sampled_columns"] = pick_cols or columns
            table_summary["sample_summary"] = summary

            for col, meta in summary.get("columns", {}).items():
                roles = meta.get("roles") or []
                if roles:
                    join_hints.append(
                        {
                            "table": name,
                            "column": col,
                            "roles": roles,
                            "samples": meta.get("samples", []),
                        }
                    )
        except Exception as exc:
            table_summary["sample_status"] = "error"
            table_summary["sample_error"] = str(exc)

        table_summaries.append(table_summary)

    # Build explicit join proposals from overlapping sample values
    role_groups = ["email", "domain_or_url", "id", "company_ref", "billing_ref", "user_ref"]
    role_index: Dict[str, List[Dict[str, Any]]] = {role: [] for role in role_groups}
    for hint in join_hints:
        samples = [s for s in hint.get("samples", []) if s and s != "<empty>"]
        if not samples:
            continue
        entry = {
            "table": hint.get("table"),
            "column": hint.get("column"),
            "samples": samples,
        }
        for role in hint.get("roles", []):
            if role in role_index:
                role_index[role].append(entry)

    for role, entries in role_index.items():
        for idx, left in enumerate(entries):
            for right in entries[idx + 1 :]:
                if left["table"] == right["table"]:
                    continue
                left_vals = {
                    _normalize_join_value(role, v)
                    for v in left.get("samples", [])
                    if _normalize_join_value(role, v)
                }
                right_vals = {
                    _normalize_join_value(role, v)
                    for v in right.get("samples", [])
                    if _normalize_join_value(role, v)
                }
                overlap = sorted(left_vals.intersection(right_vals))[:5]
                if not overlap:
                    continue
                join_candidates.append(
                    {
                        "tables": [left["table"], right["table"]],
                        "columns": [left["column"], right["column"]],
                        "role": role,
                        "overlap_samples": overlap,
                    }
                )

    return {
        "status": "ok",
        "host": host,
        "project_id": project_id,
        "projects": projects,
        "warehouse_tables": tables_data,
        "table_summaries": table_summaries,
        "join_hints": join_hints,
        "join_candidates": join_candidates,
    }
