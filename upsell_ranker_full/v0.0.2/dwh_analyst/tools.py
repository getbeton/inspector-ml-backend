from typing import Any, Dict, List, Optional

from shared.cache import cache_read_json, cache_write_json
from shared.inspector import inspector_env, inspector_get


def _extract_columns_from_meta(columns_meta: List[Dict[str, Any]]) -> List[str]:
    columns = []
    for col in columns_meta:
        name = col.get("col_name") or col.get("name") or col.get("col_id")
        if name:
            columns.append(str(name))
    return columns


def _extract_column_samples(column_meta: Dict[str, Any]) -> List[Any]:
    return column_meta.get("samples") or column_meta.get("examples") or []


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


def _summarize_columns_from_examples(
    columns_meta: List[Dict[str, Any]],
    max_examples: int = 3,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"columns": {}}
    for col in columns_meta:
        name = col.get("col_name") or col.get("name") or col.get("col_id")
        if not name:
            continue
        raw_examples = _extract_column_samples(col)
        values = []
        for value in raw_examples:
            norm = _normalize_value(value)
            if norm is not None:
                values.append(norm)
        unique_samples = []
        for value in values:
            if value not in unique_samples:
                unique_samples.append(value)
            if len(unique_samples) >= max_examples:
                break
        if not unique_samples and raw_examples:
            unique_samples = ["<empty>"]
        summary["columns"][str(name)] = {
            "type": col.get("type"),
            "non_empty": len(values),
            "empty": 0,
            "samples": unique_samples,
            "roles": _infer_column_roles(str(name), unique_samples),
        }
    return summary


def _inspector_list_tables(
    url: str,
    agent_secret: str,
    vercel_protection: str,
    session_id: str,
) -> Dict[str, Any]:
    cache_key = f"inspector_list_tables:v2:session_id={session_id}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return {"ok": True, "cached": True, "data": cached}

    data = inspector_get(
        url,
        "/api/agent/list-tables",
        agent_secret,
        vercel_protection,
        params={"session_id": session_id},
        timeout=60,
    )
    cache_write_json(cache_key, data)
    return {"ok": True, "cached": False, "data": data}


def _inspector_list_columns(
    url: str,
    agent_secret: str,
    vercel_protection: str,
    session_id: str,
    table_id: str,
) -> Dict[str, Any]:
    cache_key = f"inspector_list_columns:v2:session_id={session_id}:table_id={table_id}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return {"ok": True, "cached": True, "data": cached}

    data = inspector_get(
        url,
        "/api/agent/list-columns",
        agent_secret,
        vercel_protection,
        params={"session_id": session_id, "table_id": table_id},
        timeout=60,
    )
    cache_write_json(cache_key, data)
    return {"ok": True, "cached": False, "data": data}


def inspector_dwh_eda(
    session_id: str = "",
    max_tables: int = 40,
    sample_columns: int = 12,
    tool_context: Any = None,
) -> Dict[str, Any]:
    """
    Inspector DWH EDA using list-tables/list-columns metadata + 3-row samples.
    """
    env = inspector_env()
    url = env["url"]
    agent_secret = env["agent_secret"]
    vercel_protection = env["vercel_protection"]

    session_id = session_id.strip()
    if not session_id:
        result = {
            "status": "unavailable",
            "reason": "missing_session_id",
            "inspector_url": url,
        }
        if tool_context is not None:
            state = getattr(tool_context, "state", None)
            if state is not None:
                state["inspector_dwh_eda"] = result
        return result

    if not agent_secret:
        result = {
            "status": "unavailable",
            "reason": "missing_inspector_agent_secret",
            "inspector_url": url,
        }
        if tool_context is not None:
            state = getattr(tool_context, "state", None)
            if state is not None:
                state["inspector_dwh_eda"] = result
        return result

    tables_payload = _inspector_list_tables(url, agent_secret, vercel_protection, session_id)
    if not tables_payload.get("ok"):
        result = {
            "status": "error",
            "reason": "list_tables_failed",
            "inspector_url": url,
            "tables": tables_payload,
        }
        if tool_context is not None:
            state = getattr(tool_context, "state", None)
            if state is not None:
                state["inspector_dwh_eda"] = result
        return result

    tables_data = tables_payload.get("data") or {}
    tables = tables_data.get("tables") or []
    tables = tables[: max(1, int(max_tables))]

    table_summaries = []
    join_hints: List[Dict[str, Any]] = []
    join_candidates: List[Dict[str, Any]] = []

    for table in tables:
        table_id = (
            table.get("table_id")
            or table.get("queryable_name")
            or table.get("table_name")
            or table.get("name")
        )
        table_name = table.get("table_name") or table.get("name") or table_id
        table_summary: Dict[str, Any] = {
            "table_id": table_id,
            "name": table_name,
            "queryable_name": table.get("queryable_name") or table_id,
            "source_type": table.get("source_type"),
            "engine": table.get("engine") or table.get("source_type"),
            "total_rows": table.get("total_rows"),
            "total_bytes": table.get("total_bytes"),
        }

        if not table_id:
            table_summary["sample_status"] = "skipped_missing_table_id"
            table_summaries.append(table_summary)
            continue

        try:
            columns_payload = _inspector_list_columns(
                url, agent_secret, vercel_protection, session_id, str(table_id)
            )
            columns_data = columns_payload.get("data") or {}
            columns_meta_full = columns_data.get("columns") or []
            columns_meta = columns_meta_full
            if sample_columns:
                columns_meta = columns_meta_full[: max(1, int(sample_columns))]

            column_names = _extract_columns_from_meta(columns_meta_full)
            summary = _summarize_columns_from_examples(columns_meta)

            table_summary["sample_status"] = "ok"
            table_summary["column_count"] = len(columns_meta_full)
            table_summary["columns"] = column_names
            table_summary["column_types"] = {
                str(
                    col.get("col_name")
                    or col.get("name")
                    or col.get("col_id")
                ): col.get("type")
                for col in columns_meta_full
                if (
                    col.get("col_name")
                    or col.get("name")
                    or col.get("col_id")
                )
            }
            table_summary["sample_summary"] = summary

            for col, meta in summary.get("columns", {}).items():
                roles = meta.get("roles") or []
                if roles:
                    join_hints.append(
                        {
                            "table": table_name,
                            "table_id": table_id,
                            "column": col,
                            "roles": roles,
                            "samples": meta.get("samples", []),
                        }
                    )
        except Exception as exc:
            table_summary["sample_status"] = "error"
            table_summary["sample_error"] = str(exc)

        table_summaries.append(table_summary)

    role_groups = ["email", "domain_or_url", "id", "company_ref", "billing_ref", "user_ref"]
    role_index: Dict[str, List[Dict[str, Any]]] = {role: [] for role in role_groups}
    for hint in join_hints:
        samples = [s for s in hint.get("samples", []) if s and s != "<empty>"]
        if not samples:
            continue
        entry = {
            "table": hint.get("table"),
            "table_id": hint.get("table_id"),
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
                        "table_ids": [left.get("table_id"), right.get("table_id")],
                        "columns": [left["column"], right["column"]],
                        "role": role,
                        "overlap_samples": overlap,
                    }
                )

    result = {
        "status": "ok",
        "inspector_url": url,
        "tables": tables_data,
        "table_summaries": table_summaries,
        "join_hints": join_hints,
        "join_candidates": join_candidates,
    }
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None:
            state["inspector_dwh_eda"] = result
    return result
