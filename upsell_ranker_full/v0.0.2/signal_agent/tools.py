import json
import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError
from shared.cache import cache_read_json, cache_write_json
from shared.inspector import inspector_env, inspector_get, inspector_post

from .models import (
    CandidateSignalDraft,
    ExecutionOutcome,
    ExperimentReport,
    PromotedSignal,
    ReviewDecision,
    WarehouseProfile,
)

POLICY_VERSION = "v0.0.2"
BLOCKED_SQL_PATTERNS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "merge",
    "attach",
    "detach",
    "optimize",
)
BLOCKED_SQL_REGEX_PATTERNS = (
    (r"\bunion\b", "union_not_allowed"),
    (r"\bcast\s*\(", "cast_not_allowed"),
    (r"\bcross\s+join\b", "cross_join_not_allowed"),
    (r"\binto\s+outfile\b", "into_outfile_not_allowed"),
    (r"\bload_file\s*\(", "load_file_not_allowed"),
)
BLOCKED_FUNCTION_PATTERNS = (
    "url",
    "remote",
    "remotesecure",
    "cluster",
    "clusterallreplicas",
    "file",
    "input",
    "sleep",
    "sleepeachrow",
    "numbers",
    "numbers_mt",
    "generaterandom",
    "zeros",
    "zeros_mt",
    "arrayjoin",
)
BLOCKED_TABLE_PREFIXES = (
    "system.",
    "information_schema.",
)
STATE_LIST_KEYS = (
    "candidate_hypotheses",
    "sql_attempts",
    "policy_validations",
    "review_decisions",
    "execution_summaries",
    "promoted_signals",
    "holdout_rerun_results",
    "loop_iteration_events",
)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _to_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _artifact_base_dir() -> Path:
    override = os.getenv("SIGNAL_AGENT_ARTIFACT_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    else:
        path = Path(__file__).resolve().parent / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_id_from_state(state: Dict[str, Any]) -> str:
    run_id = str(state.get("signal_run_id") or "").strip()
    if run_id:
        return run_id
    run_id = f"run_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    state["signal_run_id"] = run_id
    return run_id


def _run_dir(state: Dict[str, Any]) -> Path:
    run_id = _run_id_from_state(state)
    path = _artifact_base_dir() / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True))
        f.write("\n")


def _state_from_context(tool_context: Any) -> Dict[str, Any]:
    if tool_context is None:
        return {}
    state = getattr(tool_context, "state", None)
    if state is None:
        return {}
    for key in STATE_LIST_KEYS:
        state.setdefault(key, [])
    state.setdefault("allowed_tables", [])
    state.setdefault("warehouse_profile", {})
    state.setdefault("tool_call_cache", {})
    state.setdefault("last_loop_status", {})
    return state


def _stable_hash(payload: Dict[str, Any]) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        raw = repr(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tool_cache_key(tool_name: str, args_payload: Dict[str, Any]) -> str:
    return f"{tool_name}:{_stable_hash(args_payload)}"


def _tool_cache_get(state: Dict[str, Any], tool_name: str, args_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cache = state.get("tool_call_cache")
    if not isinstance(cache, dict):
        return None
    key = _tool_cache_key(tool_name, args_payload)
    cached = cache.get(key)
    if not isinstance(cached, dict):
        return None
    result = dict(cached)
    result["cached_call"] = True
    return result


def _tool_cache_set(state: Dict[str, Any], tool_name: str, args_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
    cache = state.setdefault("tool_call_cache", {})
    if not isinstance(cache, dict) or not isinstance(result, dict):
        return
    key = _tool_cache_key(tool_name, args_payload)
    cache[key] = dict(result)


def _tool_exception(tool_name: str, exc: Exception, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "ok": False,
        "error": "tool_exception",
        "tool_name": tool_name,
        "detail": str(exc),
        "at": _utc_now(),
    }
    if isinstance(state, dict):
        state["non_query_failure_count"] = int(state.get("non_query_failure_count") or 0) + 1
        try:
            _record_artifact(state, "tool_errors", payload, append=True)
        except Exception:
            pass
    return payload


def _session_id_arg(session_id: str, state: Dict[str, Any]) -> str:
    provided = (session_id or "").strip()
    if provided:
        state["session_id"] = provided
        return provided
    existing = str(state.get("session_id") or "").strip()
    return existing


def _record_artifact(state: Dict[str, Any], name: str, payload: Dict[str, Any], append: bool) -> None:
    if not state:
        return
    run_dir = _run_dir(state)
    if append:
        _append_jsonl(run_dir / f"{name}.jsonl", payload)
    else:
        _write_json(run_dir / f"{name}.json", payload)


def initialize_signal_run(
    session_id: str = "",
    max_iterations: int = 8,
    target_promoted_signals: int = 3,
    too_many_failures: int = 5,
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        args_payload = {
            "session_id": (session_id or "").strip(),
            "max_iterations": int(max_iterations),
            "target_promoted_signals": int(target_promoted_signals),
            "too_many_failures": int(too_many_failures),
        }
        cached = _tool_cache_get(state, "initialize_signal_run", args_payload)
        if cached is not None:
            return cached
        sid = _session_id_arg(session_id, state)
        state["signal_run_id"] = f"run_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        for key in STATE_LIST_KEYS:
            state[key] = []
        state["allowed_tables"] = []
        state["schema_snapshot"] = {}
        state["warehouse_profile"] = {}
        state["query_count"] = 0
        state["failure_count"] = 0
        state["non_query_failure_count"] = 0
        state["iteration_index"] = 0
        state["last_loop_status"] = {}
        state["tool_call_cache"] = {}
        state["loop_limits"] = {
            "max_iterations": int(max_iterations),
            "target_promoted_signals": int(target_promoted_signals),
            "too_many_failures": int(too_many_failures),
        }
        run_id = str(state["signal_run_id"])
        payload = {
            "ok": True,
            "run_id": run_id,
            "session_id": sid,
            "loop_limits": state["loop_limits"],
            "created_at": _utc_now(),
        }
        _record_artifact(state, "run_meta", payload, append=False)
        _tool_cache_set(state, "initialize_signal_run", args_payload, payload)
        return payload
    except Exception as exc:
        return _tool_exception("initialize_signal_run", exc, state)


def _extract_table_names(list_tables_payload: Dict[str, Any]) -> List[str]:
    raw_tables = list_tables_payload.get("tables") or []
    names: List[str] = []
    for table in raw_tables:
        if not isinstance(table, dict):
            continue
        table_name = (
            table.get("queryable_name")
            or table.get("table_name")
            or table.get("name")
            or table.get("table_id")
        )
        if table_name:
            names.append(str(table_name))
    return names


def list_tables(session_id: str = "", max_tables: int = 80, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    sid = _session_id_arg(session_id, state)
    if not sid:
        return {"ok": False, "error": "missing_session_id"}
    env = inspector_env()
    if not env["agent_secret"]:
        return {"ok": False, "error": "missing_inspector_agent_secret"}
    cache_key = f"signal_list_tables:v1:session_id={sid}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        tables_payload = cached
        cached_hit = True
    else:
        try:
            tables_payload = inspector_get(
                env["url"],
                "/api/agent/list-tables",
                env["agent_secret"],
                env["vercel_protection"],
                params={"session_id": sid},
                timeout=60,
            )
        except Exception as exc:
            return {"ok": False, "error": "list_tables_failed", "detail": str(exc)}
        cache_write_json(cache_key, tables_payload)
        cached_hit = False
    table_names = _extract_table_names(tables_payload)[: max(1, int(max_tables))]
    state["allowed_tables"] = sorted(set(table_names))
    snapshot = {
        "captured_at": _utc_now(),
        "tables_raw": tables_payload.get("tables") or [],
        "table_names": state["allowed_tables"],
    }
    state["schema_snapshot"] = snapshot
    _record_artifact(state, "schema_snapshot", snapshot, append=False)
    return {
        "ok": True,
        "cached": cached_hit,
        "table_count": len(table_names),
        "tables": table_names,
    }


def describe_table(table_name: str, session_id: str = "", sample_columns: int = 40, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    sid = _session_id_arg(session_id, state)
    table_name = (table_name or "").strip()
    if not table_name:
        return {"ok": False, "error": "missing_table_name"}
    if not sid:
        return {"ok": False, "error": "missing_session_id"}
    env = inspector_env()
    if not env["agent_secret"]:
        return {"ok": False, "error": "missing_inspector_agent_secret"}
    cache_key = f"signal_describe_table:v1:session_id={sid}:table={table_name}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        columns_payload = cached
        cached_hit = True
    else:
        try:
            columns_payload = inspector_get(
                env["url"],
                "/api/agent/list-columns",
                env["agent_secret"],
                env["vercel_protection"],
                params={"session_id": sid, "table_id": table_name},
                timeout=60,
            )
        except Exception as exc:
            return {"ok": False, "error": "describe_table_failed", "detail": str(exc)}
        cache_write_json(cache_key, columns_payload)
        cached_hit = False
    columns = (columns_payload.get("columns") or [])[: max(1, int(sample_columns))]
    schema_snapshot = state.get("schema_snapshot") or {}
    table_details = schema_snapshot.get("table_details") if isinstance(schema_snapshot, dict) else None
    if not isinstance(table_details, dict):
        table_details = {}
    table_details[table_name] = {"columns": columns, "captured_at": _utc_now()}
    if isinstance(schema_snapshot, dict):
        schema_snapshot["table_details"] = table_details
        state["schema_snapshot"] = schema_snapshot
        _record_artifact(state, "schema_snapshot", schema_snapshot, append=False)
    return {
        "ok": True,
        "cached": cached_hit,
        "table_name": table_name,
        "column_count": len(columns_payload.get("columns") or []),
        "columns": columns,
    }


def _normalize_sql_value(sql: str) -> str:
    normalized = re.sub(r"\s+", " ", (sql or "").strip())
    return normalized.rstrip(";").strip()


def normalize_sql(sql: str) -> Dict[str, Any]:
    return {"ok": True, "normalized_sql": _normalize_sql_value(sql)}


def _extract_referenced_tables(sql: str) -> List[str]:
    # Approximate extraction for FROM/JOIN clauses.
    tokens = re.findall(r"\b(?:from|join)\s+([`\"\[]?[a-zA-Z0-9_.-]+[`\"\]]?)", sql, flags=re.IGNORECASE)
    tables: List[str] = []
    for token in tokens:
        cleaned = token.strip().strip("`").strip('"').strip("[").strip("]")
        if cleaned:
            tables.append(cleaned)
    return sorted(set(tables))


def _extract_cte_names(sql: str) -> List[str]:
    names = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", sql, flags=re.IGNORECASE)
    return sorted({name.lower() for name in names})


def _detect_risky_person_join(sql: str) -> List[str]:
    violations: List[str] = []
    compact = re.sub(r"\s+", " ", (sql or "").lower())
    # Inspector/PostHog commonly stores events.distinct_id as String and persons.id as UUID.
    # This join often fails with "no_common_type".
    if re.search(r"\bjoin\s+persons\b", compact) and re.search(
        r"\bon\s+[^=]*\bdistinct_id\b\s*=\s*[^=]*\bpersons?\b\.\bid\b", compact
    ):
        violations.append("risky_join_events_distinct_id_to_persons_id")
    if re.search(r"\bjoin\s+persons\b", compact) and re.search(
        r"\bon\s+[^=]*\bpersons?\b\.\bid\b\s*=\s*[^=]*\bdistinct_id\b", compact
    ):
        violations.append("risky_join_persons_id_to_events_distinct_id")
    return violations


def _has_case_without_else(sql: str) -> bool:
    compact = re.sub(r"\s+", " ", (sql or "").lower())
    # Prevent Inspector runtime failure: CASE without ELSE often compiles to multiIf with too few args.
    return bool(re.search(r"\bcase\b[\s\S]*?\bwhen\b[\s\S]*?\bthen\b(?![\s\S]*?\belse\b)[\s\S]*?\bend\b", compact))


def _has_case_expression(sql: str) -> bool:
    return bool(re.search(r"\bcase\b", (sql or "").lower()))


def _enforce_limit(sql: str, max_rows: int) -> Tuple[str, bool, Optional[int]]:
    match = re.search(r"\blimit\s+(\d+)\b", sql, flags=re.IGNORECASE)
    if not match:
        return f"{sql.rstrip()} LIMIT {max_rows}", True, None
    raw_limit = int(match.group(1))
    if raw_limit <= max_rows:
        return sql, False, raw_limit
    enforced = re.sub(r"\blimit\s+\d+\b", f"LIMIT {max_rows}", sql, count=1, flags=re.IGNORECASE)
    return enforced, True, raw_limit


def validate_sql_policy(sql: str, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    max_len = _to_int_env("SIGNAL_AGENT_SQL_MAX_LEN", 12_000)
    max_rows = _to_int_env("SIGNAL_AGENT_MAX_ROWS", 500)
    normalized_sql = _normalize_sql_value(sql)
    lower = normalized_sql.lower()
    violations: List[str] = []

    if not normalized_sql:
        violations.append("empty_sql")
    if len(normalized_sql) > max_len:
        violations.append("query_too_long")
    if ";" in normalized_sql:
        violations.append("multi_statement_not_allowed")
    if re.search(r"--|/\*|\*/|#", normalized_sql):
        violations.append("comments_not_allowed")
    if not (lower.startswith("select ") or lower.startswith("with ")):
        violations.append("must_start_with_select_or_with")
    if re.search(r";\s*\S", normalized_sql):
        violations.append("multi_statement_not_allowed")
    for keyword in BLOCKED_SQL_PATTERNS:
        if re.search(rf"\b{re.escape(keyword)}\b", lower):
            violations.append(f"blocked_keyword:{keyword}")
    for pattern, label in BLOCKED_SQL_REGEX_PATTERNS:
        if re.search(pattern, lower, flags=re.IGNORECASE):
            violations.append(label)
    for func in BLOCKED_FUNCTION_PATTERNS:
        if re.search(rf"\b{re.escape(func)}\s*\(", lower):
            violations.append(f"blocked_function:{func}")
    for prefix in BLOCKED_TABLE_PREFIXES:
        if prefix in lower:
            violations.append(f"blocked_table_prefix:{prefix.rstrip('.')}")
    if _has_case_expression(normalized_sql):
        violations.append("case_expression_not_allowed")
    if _has_case_without_else(normalized_sql):
        violations.append("case_without_else_not_allowed")
    violations.extend(_detect_risky_person_join(normalized_sql))

    referenced_tables = _extract_referenced_tables(normalized_sql)
    cte_names = set(_extract_cte_names(normalized_sql))
    allowed_tables = set(state.get("allowed_tables") or [])
    if not allowed_tables:
        violations.append("no_allowlisted_tables_discovered")
    unknown_tables = [
        table
        for table in referenced_tables
        if table not in allowed_tables and table.lower() not in cte_names
    ]
    if unknown_tables:
        violations.append("unknown_tables_referenced")

    enforced_sql, limit_applied, existing_limit = _enforce_limit(normalized_sql, max_rows)
    allowed = not violations
    rejection_class = ""
    if any(x in violations for x in ("unknown_tables_referenced", "no_allowlisted_tables_discovered")):
        rejection_class = "schema_forbidden"
    elif any(
        x in violations
        for x in (
            "union_not_allowed",
            "cast_not_allowed",
            "case_expression_not_allowed",
            "case_without_else_not_allowed",
            "cross_join_not_allowed",
            "into_outfile_not_allowed",
            "load_file_not_allowed",
        )
    ):
        rejection_class = "syntax_forbidden"
    elif violations:
        rejection_class = "safety_forbidden"
    payload = {
        "checked_at": _utc_now(),
        "policy_version": POLICY_VERSION,
        "allowed": allowed,
        "rejection_class": rejection_class,
        "violations": sorted(set(violations)),
        "normalized_sql": normalized_sql,
        "enforced_sql": enforced_sql,
        "max_query_length": max_len,
        "max_rows": max_rows,
        "limit_applied": limit_applied,
        "existing_limit": existing_limit,
        "referenced_tables": referenced_tables,
        "unknown_tables": unknown_tables,
        "scan_budget_supported": False,
        "rewrite_hints": (
            (["Inspector blocks UNION. Use a single SELECT with conditional aggregation (for example countIf/sumIf/uniqIf)."] if "union_not_allowed" in violations else [])
            + (["Inspector blocks CAST expressions. Remove CAST(...) and rely on native numeric operations or nullIf/countIf patterns."] if "cast_not_allowed" in violations else [])
            + (["Do not use CASE expressions. Use countIf/sumIf/uniqIf/avgIf only."] if "case_expression_not_allowed" in violations else [])
            + (["CASE expressions are not allowed for signal queries; use countIf/sumIf/uniqIf/avgIf instead."] if "case_without_else_not_allowed" in violations else [])
            + (
                [
                    "Avoid joining events.distinct_id to persons.id (String vs UUID mismatch). Use events.distinct_id as entity grain, or join using a verified compatible persons key."
                ]
                if (
                    "risky_join_events_distinct_id_to_persons_id" in violations
                    or "risky_join_persons_id_to_events_distinct_id" in violations
                )
                else []
            )
        ),
    }
    state.setdefault("policy_validations", []).append(payload)
    _record_artifact(state, "policy_validations", payload, append=True)
    return payload


def _execute_sql_proxy(session_id: str, sql: str) -> Dict[str, Any]:
    env = inspector_env()
    if not env["agent_secret"]:
        return {"ok": False, "error": "missing_inspector_agent_secret"}
    timeout_sec = _to_int_env("SIGNAL_AGENT_QUERY_TIMEOUT_SEC", 90)
    payload = {"session_id": session_id, "query": sql}
    cache_key = f"signal_query:v1:session_id={session_id}:query={sql}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return {
            "ok": True,
            "cached": True,
            "types": cached.get("types"),
            "results": cached.get("results", []),
        }
    try:
        resp = inspector_post(
            env["url"],
            "/api/agent/sql-proxy",
            env["agent_secret"],
            env["vercel_protection"],
            payload,
            timeout=timeout_sec,
            include_vercel=True,
        )
    except Exception as exc:
        return {"ok": False, "error": "sql_proxy_error", "detail": str(exc)}
    cache_write_json(cache_key, resp)
    return {"ok": True, "cached": False, "types": resp.get("types"), "results": resp.get("results", [])}


def run_readonly_query(
    sql: str,
    purpose: str = "",
    expected_grain: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        sid = _session_id_arg(session_id, state)
        if not sid:
            return {"ok": False, "error": "missing_session_id"}
        max_queries = _to_int_env("SIGNAL_AGENT_MAX_QUERIES", 30)
        query_count = int(state.get("query_count") or 0)
        if query_count >= max_queries:
            outcome = {
                "ok": False,
                "error": "query_budget_exceeded",
                "max_queries": max_queries,
                "at": _utc_now(),
            }
            state.setdefault("execution_summaries", []).append(outcome)
            _record_artifact(state, "execution_summaries", outcome, append=True)
            return outcome

        policy = validate_sql_policy(sql, tool_context=tool_context)
        sql_attempt = {
            "attempted_at": _utc_now(),
            "purpose": purpose,
            "expected_grain": expected_grain,
            "sql": sql,
            "policy_allowed": policy.get("allowed", False),
            "policy_violations": policy.get("violations", []),
        }
        state.setdefault("sql_attempts", []).append(sql_attempt)
        _record_artifact(state, "sql_attempts", sql_attempt, append=True)
        if not policy.get("allowed"):
            state["failure_count"] = int(state.get("failure_count") or 0) + 1
            return {
                "ok": False,
                "error": "policy_blocked",
                "rejection_class": policy.get("rejection_class", ""),
                "violations": policy.get("violations", []),
                "unknown_tables": policy.get("unknown_tables", []),
                "rewrite_hints": policy.get("rewrite_hints", []),
            }

        exec_resp = _execute_sql_proxy(sid, str(policy.get("enforced_sql") or ""))
        if not exec_resp.get("ok"):
            state["failure_count"] = int(state.get("failure_count") or 0) + 1
            outcome_payload = {
                "status": "error",
                "purpose": purpose,
                "expected_grain": expected_grain,
                "sql": policy.get("enforced_sql"),
                "error": exec_resp.get("error"),
                "detail": exec_resp.get("detail"),
                "at": _utc_now(),
            }
            state.setdefault("execution_summaries", []).append(outcome_payload)
            _record_artifact(state, "execution_summaries", outcome_payload, append=True)
            return {"ok": False, **outcome_payload}

        state["query_count"] = query_count + 1
        rows = exec_resp.get("results") or []
        types = exec_resp.get("types") or []
        outcome = ExecutionOutcome(
            status="ok",
            purpose=purpose or "signal_query",
            expected_grain=expected_grain,
            sql=str(policy.get("enforced_sql") or ""),
            row_count=len(rows),
            column_count=len(types) if isinstance(types, list) else 0,
            cached=bool(exec_resp.get("cached")),
            notes="",
        ).model_dump()
        state.setdefault("execution_summaries", []).append(outcome)
        _record_artifact(state, "execution_summaries", outcome, append=True)
        return {
            "ok": True,
            "query": policy.get("enforced_sql"),
            "purpose": purpose,
            "expected_grain": expected_grain,
            "columns": types,
            "rows": rows,
            "row_count": len(rows),
            "cached": bool(exec_resp.get("cached")),
            "policy_version": POLICY_VERSION,
        }
    except Exception as exc:
        return _tool_exception("run_readonly_query", exc, state)


def sample_rows(table_name: str, session_id: str = "", limit: int = 20, tool_context: Any = None) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), _to_int_env("SIGNAL_AGENT_MAX_ROWS", 500)))
    sql = f"SELECT * FROM {table_name.strip()} LIMIT {safe_limit}"
    return run_readonly_query(
        sql=sql,
        purpose=f"sample_rows:{table_name}",
        expected_grain="row_sample",
        session_id=session_id,
        tool_context=tool_context,
    )


def profile_events(
    table_name: str,
    time_col: str,
    event_col: str,
    distinct_keys: Optional[List[str]] = None,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    keys = [key.strip() for key in (distinct_keys or []) if key and key.strip()]
    distinct_fragments = [f"COUNT(DISTINCT {key}) AS distinct_{key}" for key in keys[:6]]
    metrics_sql = ",\n       ".join(["COUNT(*) AS event_count"] + distinct_fragments)
    sql = (
        f"SELECT dateTrunc('day', {time_col}) AS event_day,\n"
        f"       {event_col} AS event_name,\n"
        f"       {metrics_sql}\n"
        f"FROM {table_name}\n"
        f"GROUP BY event_day, event_name\n"
        f"ORDER BY event_day DESC, event_count DESC\n"
        f"LIMIT 200"
    )
    return run_readonly_query(
        sql=sql,
        purpose=f"profile_events:{table_name}",
        expected_grain="event_day_event_name",
        session_id=session_id,
        tool_context=tool_context,
    )


def _safe_model_validate(model_cls: Any, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    try:
        validated = model_cls.model_validate(payload)
    except ValidationError as exc:
        return False, {"ok": False, "error": "validation_error", "detail": str(exc)}
    return True, validated.model_dump()


def store_candidate_signal(candidate: Dict[str, Any], tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        payload = dict(candidate or {})
        args_payload = {"candidate": payload}
        cached = _tool_cache_get(state, "store_candidate_signal", args_payload)
        if cached is not None:
            return cached
        payload.setdefault("stored_at", _utc_now())
        is_promoted = bool(payload.get("promoted")) or str(payload.get("status") or "").lower() == "promoted"
        model_cls = PromotedSignal if is_promoted else CandidateSignalDraft
        ok, validated = _safe_model_validate(model_cls, payload)
        if not ok:
            state["non_query_failure_count"] = int(state.get("non_query_failure_count") or 0) + 1
            return validated

        state.setdefault("candidate_hypotheses", []).append(validated)
        _record_artifact(state, "candidate_hypotheses", validated, append=True)

        review_payload = payload.get("review_decision")
        if isinstance(review_payload, dict):
            review_ok, review_validated = _safe_model_validate(ReviewDecision, review_payload)
            if review_ok:
                state.setdefault("review_decisions", []).append(review_validated)
                _record_artifact(state, "review_decisions", review_validated, append=True)

        execution_payload = payload.get("execution_outcome")
        if isinstance(execution_payload, dict):
            exec_ok, exec_validated = _safe_model_validate(ExecutionOutcome, execution_payload)
            if exec_ok:
                state.setdefault("execution_summaries", []).append(exec_validated)
                _record_artifact(state, "execution_summaries", exec_validated, append=True)

        if is_promoted:
            promoted_ok, promoted_validated = _safe_model_validate(PromotedSignal, payload)
            if not promoted_ok:
                state["non_query_failure_count"] = int(state.get("non_query_failure_count") or 0) + 1
                return promoted_validated
            state.setdefault("promoted_signals", []).append(promoted_validated)
            _record_artifact(state, "promoted_signal_specs", promoted_validated, append=True)
            result = {"ok": True, "stored_as": "promoted_signal", "name": promoted_validated.get("name")}
            _tool_cache_set(state, "store_candidate_signal", args_payload, result)
            return result
        result = {"ok": True, "stored_as": "candidate_hypothesis", "name": validated.get("name")}
        _tool_cache_set(state, "store_candidate_signal", args_payload, result)
        return result
    except Exception as exc:
        return _tool_exception("store_candidate_signal", exc, state)


def load_existing_signals(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    promoted = state.get("promoted_signals") or []
    if promoted:
        return {"ok": True, "count": len(promoted), "signals": promoted}
    run_dir = _run_dir(state)
    path = run_dir / "promoted_signal_specs.jsonl"
    if not path.exists():
        return {"ok": True, "count": 0, "signals": []}
    signals: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            signals.append(json.loads(line))
        except Exception:
            continue
    state["promoted_signals"] = signals
    return {"ok": True, "count": len(signals), "signals": signals}


def store_warehouse_profile(profile: Dict[str, Any], tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        payload = dict(profile or {})
        args_payload = {"profile": payload}
        cached = _tool_cache_get(state, "store_warehouse_profile", args_payload)
        if cached is not None:
            return cached
        payload.setdefault("captured_at", _utc_now())
        payload.setdefault("session_id", str(state.get("session_id") or ""))
        payload.setdefault("available_tables", list(state.get("allowed_tables") or []))
        if not payload.get("primary_events_table"):
            event_tables = payload.get("event_tables")
            if isinstance(event_tables, list) and event_tables and isinstance(event_tables[0], dict):
                payload["primary_events_table"] = str(event_tables[0].get("table_name") or "")
        if not payload.get("primary_persons_table"):
            person_tables = payload.get("person_tables")
            if isinstance(person_tables, list) and person_tables and isinstance(person_tables[0], dict):
                payload["primary_persons_table"] = str(person_tables[0].get("table_name") or "")
        if not payload.get("inferred_time_column"):
            likely = payload.get("likely_semantic_columns")
            if isinstance(likely, dict):
                time_cols = likely.get("time")
                if isinstance(time_cols, list) and time_cols:
                    payload["inferred_time_column"] = str(time_cols[0])
        if not payload.get("inferred_event_column"):
            likely = payload.get("likely_semantic_columns")
            if isinstance(likely, dict):
                event_cols = likely.get("event")
                if isinstance(event_cols, list) and event_cols:
                    payload["inferred_event_column"] = str(event_cols[0])
        if not payload.get("inferred_distinct_id_column"):
            likely = payload.get("likely_semantic_columns")
            if isinstance(likely, dict):
                person_cols = likely.get("person")
                if isinstance(person_cols, list) and person_cols:
                    payload["inferred_distinct_id_column"] = str(person_cols[0])
        if not payload.get("inferred_session_column"):
            likely = payload.get("likely_semantic_columns")
            if isinstance(likely, dict):
                session_cols = likely.get("session")
                if isinstance(session_cols, list) and session_cols:
                    payload["inferred_session_column"] = str(session_cols[0])
        if not payload.get("inferred_group_column"):
            likely = payload.get("likely_semantic_columns")
            if isinstance(likely, dict):
                group_cols = likely.get("group")
                if isinstance(group_cols, list) and group_cols:
                    payload["inferred_group_column"] = str(group_cols[0])
        if not payload.get("inferred_properties_column"):
            likely = payload.get("likely_semantic_columns")
            if isinstance(likely, dict):
                prop_cols = likely.get("properties")
                if isinstance(prop_cols, list) and prop_cols:
                    payload["inferred_properties_column"] = str(prop_cols[0])
        ok, validated = _safe_model_validate(WarehouseProfile, payload)
        if not ok:
            return validated
        state["warehouse_profile"] = validated
        _record_artifact(state, "warehouse_profile", validated, append=False)
        result = {"ok": True, "warehouse_profile": validated}
        _tool_cache_set(state, "store_warehouse_profile", args_payload, result)
        return result
    except Exception as exc:
        return _tool_exception("store_warehouse_profile", exc, state)


def dedupe_candidate(sql_or_semantics: str, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    normalized = _normalize_sql_value(sql_or_semantics)
    existing = state.get("candidate_hypotheses") or []
    duplicates = []
    for idx, item in enumerate(existing):
        if not isinstance(item, dict):
            continue
        haystacks = [
            _normalize_sql_value(str(item.get("query_template") or "")),
            _normalize_sql_value(str(item.get("sql") or "")),
            _normalize_sql_value(str(item.get("interpretation") or "")),
            _normalize_sql_value(str(item.get("name") or "")),
        ]
        if normalized and normalized in haystacks:
            duplicates.append(idx)
    return {"ok": True, "is_duplicate": bool(duplicates), "duplicate_indexes": duplicates}


def _shift_param_dates(param_set: Dict[str, Any], shift_days: int) -> Dict[str, Any]:
    shifted = dict(param_set or {})
    for key, value in list(shifted.items()):
        if not isinstance(value, str):
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            continue
        try:
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        shifted[key] = (dt + timedelta(days=shift_days)).strftime("%Y-%m-%d")
    return shifted


def _render_template(template: str, params: Dict[str, Any]) -> str:
    rendered = str(template or "")
    for key, value in (params or {}).items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def rerun_promoted_signals(holdout_shift_days: int = 14, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    promoted = state.get("promoted_signals") or []
    if not promoted:
        return {"ok": True, "rerun_count": 0, "successful": 0, "results": []}

    reruns: List[Dict[str, Any]] = []
    successful = 0
    for signal in promoted:
        if not isinstance(signal, dict):
            continue
        query_template = str(signal.get("query_template") or "")
        parameter_set = signal.get("parameter_set") or {}
        shifted = _shift_param_dates(parameter_set, int(holdout_shift_days))
        sql = _render_template(query_template, shifted)
        result = run_readonly_query(
            sql=sql,
            purpose=f"holdout_rerun:{signal.get('name') or 'signal'}",
            expected_grain=str(signal.get("entity_grain") or ""),
            session_id=str(state.get("session_id") or ""),
            tool_context=tool_context,
        )
        rerun_entry = {
            "name": signal.get("name"),
            "shift_days": int(holdout_shift_days),
            "params": shifted,
            "ok": bool(result.get("ok")),
            "row_count": int(result.get("row_count") or 0),
            "error": result.get("error"),
        }
        if rerun_entry["ok"] and rerun_entry["row_count"] > 0:
            successful += 1
        reruns.append(rerun_entry)
        _record_artifact(state, "holdout_rerun_results", rerun_entry, append=True)

    state["holdout_rerun_results"] = reruns
    return {"ok": True, "rerun_count": len(reruns), "successful": successful, "results": reruns}


def get_loop_status(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        limits = state.get("loop_limits") or {}
        max_iterations = int(limits.get("max_iterations") or _to_int_env("SIGNAL_AGENT_MAX_ITERATIONS", 8))
        target_promoted = int(limits.get("target_promoted_signals") or _to_int_env("SIGNAL_AGENT_TARGET_PROMOTED", 3))
        max_failures = int(limits.get("too_many_failures") or _to_int_env("SIGNAL_AGENT_MAX_FAILURES", 5))
        min_iterations_before_exit = _to_int_env("SIGNAL_AGENT_MIN_ITERATIONS_BEFORE_EXIT", 3)
        iteration = int(state.get("iteration_index") or 0) + 1
        state["iteration_index"] = iteration
        promoted_count = len(state.get("promoted_signals") or [])
        failures = int(state.get("failure_count") or 0)
        query_count = int(state.get("query_count") or 0)
        llm_calls_used_estimate = max(int(state.get("llm_calls_used_estimate") or 0), (iteration * 3) + 1)
        state["llm_calls_used_estimate"] = llm_calls_used_estimate
        reached_limits = (
            iteration >= max_iterations
            or promoted_count >= target_promoted
            or failures >= max_failures
        )
        should_exit = reached_limits and iteration >= min_iterations_before_exit
        reason = "continue"
        if reached_limits and iteration < min_iterations_before_exit:
            reason = "continue_min_iterations_guard"
        elif iteration >= max_iterations:
            reason = "max_iterations_reached"
        elif promoted_count >= target_promoted:
            reason = "target_promoted_signals_reached"
        elif failures >= max_failures:
            reason = "too_many_failures"
        status_payload = {
            "ok": True,
            "at": _utc_now(),
            "iteration_index": iteration,
            "promoted_count": promoted_count,
            "failure_count": failures,
            "non_query_failure_count": int(state.get("non_query_failure_count") or 0),
            "query_count": query_count,
            "llm_calls_used_estimate": llm_calls_used_estimate,
            "limits": {
                "max_iterations": max_iterations,
                "target_promoted_signals": target_promoted,
                "too_many_failures": max_failures,
                "min_iterations_before_exit": min_iterations_before_exit,
            },
            "should_exit": should_exit,
            "reason": reason,
        }
        state["last_loop_status"] = status_payload
        state.setdefault("loop_iteration_events", []).append(status_payload)
        _record_artifact(state, "loop_iteration_events", status_payload, append=True)
        return status_payload
    except Exception as exc:
        return _tool_exception("get_loop_status", exc, state)


def _summary_markdown(report: Dict[str, Any]) -> str:
    return (
        "# Signal Agent Experiment Summary\n\n"
        f"- Run ID: {report.get('run_id', '')}\n"
        f"- Candidates proposed: {report.get('candidates_proposed', 0)}\n"
        f"- Policy blocked: {report.get('policy_blocked', 0)}\n"
        f"- Reviewer rejected: {report.get('reviewer_rejected', 0)}\n"
        f"- Executed successfully: {report.get('executed_successfully', 0)}\n"
        f"- Promoted signals: {report.get('promoted', 0)}\n"
        f"- Holdout rerun successful: {report.get('rerun_successful', 0)}\n"
        f"- Holdout rerun success rate: {report.get('rerun_success_rate', 0.0):.2f}\n"
        f"- Hypothesis passed: {report.get('hypothesis_passed', False)}\n"
        f"- Stop reason: {report.get('stop_reason', '')}\n"
        f"- Stop iteration: {report.get('stop_iteration', 0)}\n"
        f"- Loop iterations observed: {report.get('loop_iterations_observed', 0)}\n"
        f"- Query count total: {report.get('query_count_total', 0)}\n"
        f"- LLM calls used (estimate): {report.get('llm_calls_used_estimate', 0)}\n"
    )


def finalize_experiment_report(holdout_shift_days: int = 14, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        args_payload = {"holdout_shift_days": int(holdout_shift_days)}
        cached = _tool_cache_get(state, "finalize_experiment_report", args_payload)
        if cached is not None:
            return cached
        existing_report = state.get("experiment_report")
        if isinstance(existing_report, dict) and existing_report.get("run_id"):
            existing_promoted = existing_report.get("promoted_signals")
            if not isinstance(existing_promoted, list):
                existing_promoted = state.get("promoted_signals") or []
            summary_path = _run_dir(state) / "summary.md"
            if not summary_path.exists():
                summary_path.write_text(_summary_markdown(existing_report), encoding="utf-8")
            result = {
                "ok": True,
                "run_id": existing_report.get("run_id"),
                "promoted_signals": existing_promoted,
                "experiment_report_metrics": {
                    "candidates_proposed": existing_report.get("candidates_proposed", 0),
                    "policy_blocked": existing_report.get("policy_blocked", 0),
                    "reviewer_rejected": existing_report.get("reviewer_rejected", 0),
                    "executed_successfully": existing_report.get("executed_successfully", 0),
                    "promoted": existing_report.get("promoted", 0),
                    "rerun_successful": existing_report.get("rerun_successful", 0),
                    "rerun_success_rate": existing_report.get("rerun_success_rate", 0.0),
                    "hypothesis_passed": existing_report.get("hypothesis_passed", False),
                },
                "experiment_report": existing_report,
                "summary_path": str(summary_path),
                "cached_call": True,
            }
            _tool_cache_set(state, "finalize_experiment_report", args_payload, result)
            return result

        rerun = rerun_promoted_signals(holdout_shift_days=holdout_shift_days, tool_context=tool_context)
        candidates = state.get("candidate_hypotheses") or []
        policy_validations = state.get("policy_validations") or []
        reviews = state.get("review_decisions") or []
        executions = state.get("execution_summaries") or []
        promoted = state.get("promoted_signals") or []
        rerun_results = state.get("holdout_rerun_results") or []
        loop_events = state.get("loop_iteration_events") or []
        last_loop_status = state.get("last_loop_status") or {}

        policy_blocked = sum(1 for x in policy_validations if isinstance(x, dict) and not x.get("allowed"))
        reviewer_rejected = sum(
            1
            for x in reviews
            if isinstance(x, dict) and str(x.get("decision") or "").lower() == "reject"
        )
        executed_successfully = sum(
            1
            for x in executions
            if isinstance(x, dict) and str(x.get("status") or "").lower() == "ok"
        )
        rerun_successful = sum(
            1
            for x in rerun_results
            if isinstance(x, dict) and x.get("ok") and int(x.get("row_count") or 0) > 0
        )
        rerun_success_rate = (rerun_successful / len(promoted)) if promoted else 0.0
        report_payload = ExperimentReport(
            run_id=_run_id_from_state(state),
            generated_at=_utc_now(),
            candidates_proposed=len(candidates),
            policy_blocked=policy_blocked,
            reviewer_rejected=reviewer_rejected,
            executed_successfully=executed_successfully,
            promoted=len(promoted),
            rerun_successful=rerun_successful,
            rerun_success_rate=rerun_success_rate,
            hypothesis_passed=(len(promoted) >= 3 and rerun_success_rate >= 0.60),
            notes=[
                f"Holdout rerun attempted for {rerun.get('rerun_count', 0)} promoted signals with shift_days={holdout_shift_days}.",
                "Deterministic SQL policy is enforced in validate_sql_policy and run_readonly_query.",
            ],
        ).model_dump()
        report_payload["warehouse_profile"] = state.get("warehouse_profile") or {}
        report_payload["promoted_signals"] = promoted
        report_payload["holdout_rerun_results"] = rerun_results
        report_payload["stop_reason"] = str(last_loop_status.get("reason") or "")
        report_payload["stop_iteration"] = int(last_loop_status.get("iteration_index") or 0)
        report_payload["loop_iterations_observed"] = len(loop_events)
        report_payload["query_count_total"] = int(state.get("query_count") or 0)
        report_payload["llm_calls_used_estimate"] = int(state.get("llm_calls_used_estimate") or 0)
        state["experiment_report"] = report_payload
        _record_artifact(state, "experiment_report", report_payload, append=False)

        summary_path = _run_dir(state) / "summary.md"
        summary_path.write_text(_summary_markdown(report_payload), encoding="utf-8")
        result = {
            "ok": True,
            "run_id": report_payload.get("run_id"),
            "promoted_signals": promoted,
            "experiment_report_metrics": {
                "candidates_proposed": report_payload.get("candidates_proposed", 0),
                "policy_blocked": report_payload.get("policy_blocked", 0),
                "reviewer_rejected": report_payload.get("reviewer_rejected", 0),
                "executed_successfully": report_payload.get("executed_successfully", 0),
                "promoted": report_payload.get("promoted", 0),
                "rerun_successful": report_payload.get("rerun_successful", 0),
                "rerun_success_rate": report_payload.get("rerun_success_rate", 0.0),
                "hypothesis_passed": report_payload.get("hypothesis_passed", False),
                "stop_reason": report_payload.get("stop_reason", ""),
                "stop_iteration": report_payload.get("stop_iteration", 0),
                "loop_iterations_observed": report_payload.get("loop_iterations_observed", 0),
                "query_count_total": report_payload.get("query_count_total", 0),
                "llm_calls_used_estimate": report_payload.get("llm_calls_used_estimate", 0),
            },
            "experiment_report": report_payload,
            "summary_path": str(summary_path),
        }
        _tool_cache_set(state, "finalize_experiment_report", args_payload, result)
        return result
    except Exception as exc:
        return _tool_exception("finalize_experiment_report", exc, state)
