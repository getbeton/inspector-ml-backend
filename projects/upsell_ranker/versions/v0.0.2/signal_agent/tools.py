"""Read-only signal discovery helpers with caching, policy checks, and artifacts."""

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
    BatchedHypothesesPayload,
    BatchedHypothesisDraft,
    CandidateSignalDraft,
    ExecutionOutcome,
    ExperimentReport,
    PromotedSignal,
    ReviewDecision,
    RicePrioritizationPayload,
    RiceRanking,
    SignalObjective,
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
    state.setdefault("signal_objective", {})
    state.setdefault("latest_event_profile", {})
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


def _truncate_rows_for_llm(rows: List[Any]) -> List[Any]:
    max_rows = _to_int_env("SIGNAL_AGENT_LLM_ROW_RETURN_LIMIT", 12)
    return list(rows[: max(1, max_rows)])


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
        state["signal_objective"] = {}
        state["latest_event_profile"] = {}
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
    # Inspector's sql-proxy returns column metadata under `columns` (PostHog
    # naming), older paths returned `types`. Accept either.
    column_meta = resp.get("types") or resp.get("columns") or []
    return {"ok": True, "cached": False, "types": column_meta, "results": resp.get("results", [])}


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
        llm_rows = _truncate_rows_for_llm(rows)
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
            "rows": llm_rows,
            "row_count": len(rows),
            "returned_row_count": len(llm_rows),
            "cached": bool(exec_resp.get("cached")),
            "policy_version": POLICY_VERSION,
        }
    except Exception as exc:
        return _tool_exception("run_readonly_query", exc, state)


def sample_rows(table_name: str, session_id: str = "", limit: int = 20, tool_context: Any = None) -> Dict[str, Any]:
    default_limit = _to_int_env("SIGNAL_AGENT_SAMPLE_ROWS_LIMIT", 5)
    requested_limit = int(limit) if int(limit) > 0 else default_limit
    safe_limit = max(1, min(requested_limit, _to_int_env("SIGNAL_AGENT_MAX_ROWS", 500)))
    sql = f"SELECT * FROM {table_name.strip()} LIMIT {safe_limit}"
    return run_readonly_query(
        sql=sql,
        purpose=f"sample_rows:{table_name}",
        expected_grain="row_sample",
        session_id=session_id,
        tool_context=tool_context,
    )


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _extract_row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if isinstance(row, list) and 0 <= index < len(row):
        return row[index]
    return None


def _extract_event_profile(rows: List[Any], event_col_name: str) -> List[Dict[str, Any]]:
    event_totals: Dict[str, Dict[str, Any]] = {}
    normalized_col = (event_col_name or "").strip() or "event_name"
    for row in rows:
        raw_event = _extract_row_value(row, "event_name", 1)
        if raw_event is None:
            raw_event = _extract_row_value(row, normalized_col, 1)
        event_name = str(raw_event or "").strip()
        if not event_name:
            continue
        event_count = _coerce_int(_extract_row_value(row, "event_count", 2), 0)
        distinct_hint = _coerce_int(_extract_row_value(row, "distinct_distinct_id", 3), 0)
        bucket = event_totals.setdefault(
            event_name,
            {"event_name": event_name, "event_count": 0, "distinct_users_hint": 0},
        )
        bucket["event_count"] = int(bucket["event_count"]) + event_count
        bucket["distinct_users_hint"] = int(bucket["distinct_users_hint"]) + distinct_hint
    return sorted(event_totals.values(), key=lambda x: int(x.get("event_count") or 0), reverse=True)[:40]


def _event_name_lexical_score(event_name: str, success_event_hint: str) -> float:
    lower = (event_name or "").strip().lower()
    if not lower:
        return 0.0
    hint = (success_event_hint or "").strip().lower() or "user_signup"
    if lower == hint:
        return 1.0
    if hint in lower:
        return 0.95
    keyword_weights = (
        ("user_signup", 0.95),
        ("signup", 0.9),
        ("sign_up", 0.9),
        ("sign-up", 0.9),
        ("register", 0.85),
        ("subscription", 0.8),
        ("subscribe", 0.8),
        ("upgrade", 0.75),
        ("purchase", 0.7),
        ("checkout", 0.7),
        ("billing", 0.55),
    )
    for keyword, score in keyword_weights:
        if keyword in lower:
            return score
    return 0.0


def _infer_success_event_from_profile(
    event_profile: List[Dict[str, Any]], success_event_hint: str
) -> Tuple[str, float, bool, List[Dict[str, Any]]]:
    if not event_profile:
        fallback = (success_event_hint or "").strip() or "user_signup"
        return fallback, 0.25, True, []
    total_events = max(1, sum(_coerce_int(item.get("event_count"), 0) for item in event_profile))
    scored: List[Dict[str, Any]] = []
    for item in event_profile:
        event_name = str(item.get("event_name") or "").strip()
        if not event_name:
            continue
        lexical_score = _event_name_lexical_score(event_name, success_event_hint)
        volume_share = min(0.2, _coerce_float(item.get("event_count"), 0.0) / total_events)
        score = lexical_score + volume_share
        scored.append(
            {
                "event_name": event_name,
                "event_count": _coerce_int(item.get("event_count"), 0),
                "distinct_users_hint": _coerce_int(item.get("distinct_users_hint"), 0),
                "lexical_score": round(lexical_score, 4),
                "score": round(score, 4),
            }
        )
    scored.sort(key=lambda x: _coerce_float(x.get("score"), 0.0), reverse=True)
    best = scored[0] if scored else {}
    best_name = str(best.get("event_name") or "").strip()
    best_score = _coerce_float(best.get("score"), 0.0)
    if best_name and best_score >= 0.7:
        confidence = min(0.99, max(0.55, best_score))
        return best_name, confidence, False, scored[:8]
    fallback = (success_event_hint or "").strip() or "user_signup"
    fallback_bonus = 0.15 if any(str(x.get("event_name") or "").strip().lower() == fallback.lower() for x in scored) else 0.0
    return fallback, min(0.75, 0.35 + fallback_bonus), True, scored[:8]


def _build_cohort_sql_snippets(
    events_table: str,
    person_col: str,
    time_col: str,
    event_col: str,
    session_col: str,
    success_event_name: str,
    failure_inactivity_days: int,
) -> Dict[str, str]:
    table = events_table.strip() or "events"
    pid = person_col.strip() or "distinct_id"
    ts = time_col.strip() or "timestamp"
    ev = event_col.strip() or "event"
    sess = session_col.strip() or "$session_id"
    success_value = success_event_name.replace("'", "\\'")
    inactive_days = max(1, int(failure_inactivity_days))
    return {
        "success_users_30d": (
            f"SELECT DISTINCT {pid} AS person_id "
            f"FROM {table} "
            f"WHERE {ev} = '{success_value}' "
            f"AND {ts} >= now() - INTERVAL 30 DAY"
        ),
        "failure_users_inactive_gt_days": (
            f"SELECT {pid} AS person_id "
            f"FROM {table} "
            f"GROUP BY {pid} "
            f"HAVING max({ts}) < now() - INTERVAL {inactive_days} DAY "
            f"AND {pid} NOT IN ("
            f"SELECT DISTINCT {pid} FROM {table} WHERE {ev} = '{success_value}'"
            f")"
        ),
        "grey_users_active_non_success_30d": (
            f"SELECT DISTINCT {pid} AS person_id "
            f"FROM {table} "
            f"WHERE {ts} >= now() - INTERVAL 30 DAY "
            f"AND {pid} NOT IN ("
            f"SELECT DISTINCT {pid} FROM {table} WHERE {ev} = '{success_value}'"
            f") "
            f"AND {pid} NOT IN ("
            f"SELECT {pid} FROM {table} GROUP BY {pid} "
            f"HAVING max({ts}) < now() - INTERVAL {inactive_days} DAY"
            f")"
        ),
        "precursor_path_same_session": (
            f"WITH success AS ("
            f"SELECT {pid} AS person_id, {sess} AS session_id, min({ts}) AS success_ts "
            f"FROM {table} "
            f"WHERE {ev} = '{success_value}' "
            f"GROUP BY {pid}, {sess}"
            f") "
            f"SELECT e.{ev} AS precursor_event, countDistinct(e.{pid}) AS users "
            f"FROM {table} e "
            f"INNER JOIN success s ON e.{pid} = s.person_id AND e.{sess} = s.session_id "
            f"WHERE e.{ts} < s.success_ts "
            f"AND e.{ts} >= s.success_ts - INTERVAL 1 DAY "
            f"GROUP BY precursor_event "
            f"ORDER BY users DESC LIMIT 50"
        ),
        "precursor_path_7d": (
            f"WITH success AS ("
            f"SELECT {pid} AS person_id, min({ts}) AS success_ts "
            f"FROM {table} "
            f"WHERE {ev} = '{success_value}' "
            f"GROUP BY {pid}"
            f") "
            f"SELECT e.{ev} AS precursor_event, countDistinct(e.{pid}) AS users "
            f"FROM {table} e "
            f"INNER JOIN success s ON e.{pid} = s.person_id "
            f"WHERE e.{ts} < s.success_ts "
            f"AND e.{ts} >= s.success_ts - INTERVAL 7 DAY "
            f"GROUP BY precursor_event "
            f"ORDER BY users DESC LIMIT 50"
        ),
    }


def profile_events(
    table_name: str,
    time_col: str,
    event_col: str,
    distinct_keys: Optional[List[str]] = None,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    keys = [key.strip() for key in (distinct_keys or []) if key and key.strip()]
    keys = keys[: max(1, _to_int_env("SIGNAL_AGENT_PROFILE_DISTINCT_KEY_LIMIT", 3))]
    distinct_fragments = [f"COUNT(DISTINCT {key}) AS distinct_{key}" for key in keys[:6]]
    profile_limit = max(10, _to_int_env("SIGNAL_AGENT_PROFILE_EVENTS_LIMIT", 60))
    metrics_sql = ",\n       ".join(["COUNT(*) AS event_count"] + distinct_fragments)
    sql = (
        f"SELECT dateTrunc('day', {time_col}) AS event_day,\n"
        f"       {event_col} AS event_name,\n"
        f"       {metrics_sql}\n"
        f"FROM {table_name}\n"
        f"GROUP BY event_day, event_name\n"
        f"ORDER BY event_day DESC, event_count DESC\n"
        f"LIMIT {profile_limit}"
    )
    result = run_readonly_query(
        sql=sql,
        purpose=f"profile_events:{table_name}",
        expected_grain="event_day_event_name",
        session_id=session_id,
        tool_context=tool_context,
    )
    if not result.get("ok"):
        return result
    rows = result.get("rows") or []
    event_profile = _extract_event_profile(rows, event_col)
    snapshot = {
        "captured_at": _utc_now(),
        "table_name": table_name,
        "time_col": time_col,
        "event_col": event_col,
        "distinct_keys": keys,
        "top_events": event_profile,
    }
    state["latest_event_profile"] = snapshot
    _record_artifact(state, "event_profile_snapshot", snapshot, append=False)
    return {**result, "top_events": event_profile[:12]}


def infer_signal_objective(
    success_event_hint: str = "user_signup",
    failure_inactivity_days: int = 7,
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    profile = state.get("warehouse_profile") or {}
    events_meta = profile.get("events_table") if isinstance(profile.get("events_table"), dict) else {}
    latest_profile = state.get("latest_event_profile") or {}
    events_table = (
        str(profile.get("primary_events_table") or "")
        or str(events_meta.get("table_name") or "")
        or str(latest_profile.get("table_name") or "")
        or "events"
    )
    time_col = (
        str(profile.get("inferred_time_column") or "")
        or str(events_meta.get("time_column") or "")
        or str(latest_profile.get("time_col") or "")
        or "timestamp"
    )
    event_col = (
        str(profile.get("inferred_event_column") or "")
        or str(events_meta.get("event_column") or "")
        or str(latest_profile.get("event_col") or "")
        or "event"
    )
    person_col = (
        str(profile.get("inferred_distinct_id_column") or "")
        or str(events_meta.get("person_column") or "")
        or "distinct_id"
    )
    session_col = (
        str(profile.get("inferred_session_column") or "")
        or str(events_meta.get("session_column") or "")
        or "$session_id"
    )
    top_events = latest_profile.get("top_events") if isinstance(latest_profile.get("top_events"), list) else []
    success_event, confidence, fallback_used, candidates = _infer_success_event_from_profile(
        event_profile=top_events,
        success_event_hint=success_event_hint,
    )
    payload = {
        "success_event_name": success_event,
        "success_event_confidence": round(confidence, 4),
        "success_fallback_used": bool(fallback_used),
        "success_event_candidates": candidates,
        "failure_inactivity_days": max(1, int(failure_inactivity_days)),
        "success_overrides_failure": True,
        "events_table": events_table,
        "events_time_column": time_col,
        "events_event_column": event_col,
        "events_person_column": person_col,
        "events_session_column": session_col,
        "objective_notes": [
            "Success target inferred from profiled events with lexical+volume scoring.",
            "Failure cohort is inactivity > failure_inactivity_days and excludes any success user.",
        ],
        "cohort_sql_snippets": _build_cohort_sql_snippets(
            events_table=events_table,
            person_col=person_col,
            time_col=time_col,
            event_col=event_col,
            session_col=session_col,
            success_event_name=success_event,
            failure_inactivity_days=max(1, int(failure_inactivity_days)),
        ),
        "captured_at": _utc_now(),
    }
    ok, validated = _safe_model_validate(SignalObjective, payload)
    if not ok:
        state["non_query_failure_count"] = int(state.get("non_query_failure_count") or 0) + 1
        return validated
    state["signal_objective"] = validated
    _record_artifact(state, "signal_objective", validated, append=False)
    return {"ok": True, "signal_objective": validated}


def get_signal_objective(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    objective = state.get("signal_objective")
    if isinstance(objective, dict) and objective.get("success_event_name"):
        return {"ok": True, "signal_objective": objective}
    inferred = infer_signal_objective(tool_context=tool_context)
    if not inferred.get("ok"):
        return inferred
    return {"ok": True, "signal_objective": inferred.get("signal_objective", {})}


def get_warehouse_profile(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    profile = state.get("warehouse_profile") or {}
    return {"ok": True, "warehouse_profile": profile}


def _safe_model_validate(model_cls: Any, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    try:
        validated = model_cls.model_validate(payload)
    except ValidationError as exc:
        return False, {"ok": False, "error": "validation_error", "detail": str(exc)}
    return True, validated.model_dump()


def _normalize_review_decision(review_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(review_payload or {})
    decision_raw = str(payload.get("decision") or payload.get("status") or "").strip().lower()
    promoted_flag = bool(payload.get("promoted"))
    approve_tokens = {"approve", "approved", "promote", "promoted", "accept", "accepted", "pass", "passed"}
    decision = "approve" if (decision_raw in approve_tokens or promoted_flag) else "reject"

    reasons_raw = payload.get("reasons")
    reasons: List[str] = []
    if isinstance(reasons_raw, list):
        reasons = [str(item).strip() for item in reasons_raw if str(item).strip()]
    elif isinstance(payload.get("reason"), str) and payload.get("reason", "").strip():
        reasons = [str(payload.get("reason")).strip()]

    required_fixes_raw = payload.get("required_fixes")
    required_fixes: List[str] = []
    if isinstance(required_fixes_raw, list):
        required_fixes = [str(item).strip() for item in required_fixes_raw if str(item).strip()]
    elif isinstance(payload.get("required_fix"), str) and payload.get("required_fix", "").strip():
        required_fixes = [str(payload.get("required_fix")).strip()]

    promotion_readiness = bool(payload.get("promotion_readiness")) or decision == "approve"
    normalized = {
        "decision": decision,
        "reasons": reasons,
        "required_fixes": required_fixes,
        "promotion_readiness": promotion_readiness,
    }
    if isinstance(payload.get("status"), str):
        normalized["status"] = payload.get("status")
    if isinstance(payload.get("reason"), str):
        normalized["reason"] = payload.get("reason")
    normalized["promoted"] = promoted_flag
    return normalized


def _candidate_mentions_success_target(payload: Dict[str, Any], objective: Dict[str, Any]) -> bool:
    target = str(objective.get("success_event_name") or "").strip().lower()
    if not target:
        return True
    direct_target = str(payload.get("target_event") or "").strip().lower()
    if direct_target == target:
        return True
    corpus = " ".join(
        [
            str(payload.get("name") or ""),
            str(payload.get("interpretation") or ""),
            str(payload.get("query_template") or ""),
            str(payload.get("entity_grain") or ""),
        ]
    ).lower()
    return target in corpus


def _candidate_has_cohort_evidence(payload: Dict[str, Any]) -> bool:
    """Promotion gate. Requires REAL statistical significance, not just the
    presence of the canonical column names. Specifically:

      1. The candidate's promotion_evidence dict must contain the six raw
         cohort counts (signal_*/control_*) — these come from
         `_extract_cohort_evidence`, which only populates them when the
         Explorer's SQL emitted columns named that way over real data.
      2. The computed `significant` flag must be True. `_compute_cohort_metrics`
         sets it based on:
           - p-value ≤ SIG_LEVEL (default 0.05, two-tailed two-proportion z-test)
           - lift ≥ MIN_LIFT     (default 1.10, i.e. ≥ 10% relative)
           - both cohorts have n ≥ MIN_COHORT_N (default 30) so the test has power.

    A candidate that emits the column NAMES but with hardcoded scalar
    literals will fail #2: hardcoded values produce a degenerate test
    (zero variance, infinite z, or low_power), and the gate stays False.
    """
    evidence = payload.get("promotion_evidence")
    if not isinstance(evidence, dict):
        return False

    raw_keys = (
        "signal_success", "signal_failure", "signal_grey",
        "control_success", "control_failure", "control_grey",
    )
    if not any(k in evidence for k in raw_keys):
        return False

    return bool(evidence.get("significant"))
    return bool(evidence.get("cohort_metrics"))


def store_candidate_signal(candidate: Dict[str, Any], tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        payload = dict(candidate or {})
        args_payload = {"candidate": payload}
        cached = _tool_cache_get(state, "store_candidate_signal", args_payload)
        if cached is not None:
            return cached
        payload.setdefault("stored_at", _utc_now())
        requested_promoted = bool(payload.get("promoted")) or str(payload.get("status") or "").lower() == "promoted"
        review_payload = payload.get("review_decision")
        review_validated: Optional[Dict[str, Any]] = None
        if isinstance(review_payload, dict):
            normalized_review = _normalize_review_decision(review_payload)
            review_ok, review_candidate = _safe_model_validate(ReviewDecision, normalized_review)
            if review_ok:
                review_validated = review_candidate
                payload["review_decision"] = normalized_review
            else:
                payload["review_decision"] = normalized_review

        review_allows_promotion = bool(
            review_validated
            and str(review_validated.get("decision") or "").strip().lower() == "approve"
            and bool(review_validated.get("promotion_readiness"))
        )
        objective = state.get("signal_objective") if isinstance(state.get("signal_objective"), dict) else {}
        conversion_aligned = _candidate_mentions_success_target(payload, objective)
        has_cohort_evidence = _candidate_has_cohort_evidence(payload)
        promotion_block_reason = ""
        if requested_promoted and not review_allows_promotion:
            promotion_block_reason = "promotion_requires_approved_review_decision"
        elif requested_promoted and not conversion_aligned:
            promotion_block_reason = "candidate_missing_success_target_alignment"
        elif requested_promoted and not has_cohort_evidence:
            promotion_block_reason = "candidate_missing_cohort_evidence"
        is_promoted = requested_promoted and not promotion_block_reason
        if promotion_block_reason:
            payload["promoted"] = False
            payload["status"] = "candidate"
            payload["promotion_block_reason"] = promotion_block_reason
        model_cls = PromotedSignal if is_promoted else CandidateSignalDraft
        ok, validated = _safe_model_validate(model_cls, payload)
        if not ok:
            state["non_query_failure_count"] = int(state.get("non_query_failure_count") or 0) + 1
            return validated

        state.setdefault("candidate_hypotheses", []).append(validated)
        _record_artifact(state, "candidate_hypotheses", validated, append=True)

        if review_validated:
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
        if promotion_block_reason:
            result["promotion_blocked"] = True
            result["promotion_block_reason"] = promotion_block_reason
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
        objective = state.get("signal_objective") if isinstance(state.get("signal_objective"), dict) else {}
        if objective:
            if not payload.get("success_event_name"):
                payload["success_event_name"] = str(objective.get("success_event_name") or "")
            if payload.get("success_event_confidence") in (None, ""):
                payload["success_event_confidence"] = _coerce_float(objective.get("success_event_confidence"), 0.0)
            if payload.get("success_fallback_used") in (None, ""):
                payload["success_fallback_used"] = bool(objective.get("success_fallback_used"))
            if payload.get("failure_inactivity_days") in (None, ""):
                payload["failure_inactivity_days"] = _coerce_int(objective.get("failure_inactivity_days"), 7)
            if payload.get("success_overrides_failure") in (None, ""):
                payload["success_overrides_failure"] = bool(objective.get("success_overrides_failure", True))
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


import math

# Canonical SQL output convention for promotion-eligible candidates.
#
# Mason asks the Explorer to emit only the SIX raw counts; everything
# else (lift, delta, z-score, p-value, significance flag) is computed
# in Python so the model can't fabricate the discrimination metrics.
_COHORT_RAW_KEYS = (
    "signal_success",   # users with the signal AND has_success=1
    "signal_failure",   # users with the signal AND has_failure=1 (inactive ≥ N days, no success)
    "signal_grey",      # users with the signal AND still in-flight (active in last N days, no success)
    "control_success",  # users WITHOUT the signal AND has_success=1
    "control_failure",  # users WITHOUT the signal AND has_failure=1
    "control_grey",     # users WITHOUT the signal AND in-flight
)

# Tunables for the statistical promotion gate. Override via env.
_SIG_LEVEL = 0.05         # two-tailed p-value cutoff
_MIN_LIFT = 1.10          # minimum effect size (10% relative lift)
_MIN_COHORT_N = 30        # minimum n per cohort for the test to apply


def _normal_cdf(x: float) -> float:
    """Standard-normal CDF via erf. Same precision as scipy for our needs."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _two_proportion_z(s1: int, n1: int, s2: int, n2: int) -> Tuple[float, float]:
    """Two-proportion z-test. Returns (z, two-tailed p-value).
    s1/n1 = with-signal successes/total, s2/n2 = control successes/total.
    """
    if n1 <= 0 or n2 <= 0:
        return 0.0, 1.0
    p1 = s1 / n1
    p2 = s2 / n2
    p_pooled = (s1 + s2) / (n1 + n2)
    if p_pooled in (0.0, 1.0):
        return 0.0, 1.0
    se = math.sqrt(p_pooled * (1.0 - p_pooled) * (1.0 / n1 + 1.0 / n2))
    if se == 0.0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    p_value = 2.0 * (1.0 - _normal_cdf(abs(z)))
    return z, p_value


def _compute_cohort_metrics(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Given the six raw counts, compute lift / delta / z / p_value / sig flag."""
    s_succ = int(raw.get("signal_success") or 0)
    s_fail = int(raw.get("signal_failure") or 0)
    s_grey = int(raw.get("signal_grey") or 0)
    c_succ = int(raw.get("control_success") or 0)
    c_fail = int(raw.get("control_failure") or 0)
    c_grey = int(raw.get("control_grey") or 0)

    n_signal = s_succ + s_fail
    n_control = c_succ + c_fail
    signal_cr = s_succ / n_signal if n_signal else 0.0
    control_cr = c_succ / n_control if n_control else 0.0
    lift = (signal_cr / control_cr) if control_cr > 0 else 0.0
    delta_pp = signal_cr - control_cr

    z, p_value = _two_proportion_z(s_succ, n_signal, c_succ, n_control)

    min_n = max(n_signal, 0), max(n_control, 0)
    low_power = (n_signal < _MIN_COHORT_N) or (n_control < _MIN_COHORT_N)
    significant = (p_value <= _SIG_LEVEL) and (lift >= _MIN_LIFT) and not low_power

    return {
        # raw counts (round-tripped for downstream consumers)
        "signal_success": s_succ,
        "signal_failure": s_fail,
        "signal_grey": s_grey,
        "control_success": c_succ,
        "control_failure": c_fail,
        "control_grey": c_grey,
        # derived metrics
        "n_signal": n_signal,
        "n_control": n_control,
        "signal_cr": round(signal_cr, 6),
        "control_cr": round(control_cr, 6),
        "lift": round(lift, 6),
        "delta_pp": round(delta_pp, 6),
        "z_score": round(z, 4),
        "p_value": round(p_value, 6),
        "significant": bool(significant),
        "low_power": bool(low_power),
        "sig_level": _SIG_LEVEL,
        "min_lift_threshold": _MIN_LIFT,
        # legacy aliases so existing dashboards / scoring keep working
        "success_cohort_size": s_succ,
        "failure_cohort_size": s_fail,
        "grey_cohort_size": s_grey,
        "conversion_lift": round(lift, 6),
        "conversion_rate_delta": round(delta_pp, 6),
        "precision_proxy": round(signal_cr, 6),
    }


def _extract_cohort_evidence(columns: Any, rows: Any) -> Dict[str, Any]:
    """Read the six raw cohort counts from a HogQL result, then derive the
    full statistical evidence dict via `_compute_cohort_metrics`.

    The Explorer is instructed to author SQL that produces columns named
    exactly:
       signal_success, signal_failure, signal_grey,
       control_success, control_failure, control_grey
    (case-insensitive). Returns {} when none of these are present —
    `_candidate_has_cohort_evidence` will then reject promotion.
    """
    if not isinstance(rows, list) or not rows:
        return {}
    first = rows[0]
    if isinstance(first, dict):
        lookup = {str(k).strip().lower(): v for k, v in first.items()}
    elif isinstance(first, (list, tuple)):
        col_names: List[str] = []
        if isinstance(columns, list):
            for entry in columns:
                if isinstance(entry, dict):
                    col_names.append(str(entry.get("name") or "").strip().lower())
                elif isinstance(entry, (list, tuple)) and entry:
                    col_names.append(str(entry[0]).strip().lower())
                else:
                    col_names.append(str(entry).strip().lower())
        lookup = {col_names[i]: first[i] for i in range(min(len(col_names), len(first)))}
    else:
        return {}

    raw: Dict[str, Any] = {}
    for key in _COHORT_RAW_KEYS:
        if key in lookup and lookup[key] is not None:
            try:
                raw[key] = int(lookup[key])
            except (TypeError, ValueError):
                try:
                    raw[key] = int(float(lookup[key]))
                except (TypeError, ValueError):
                    pass

    if not raw:
        return {}

    return _compute_cohort_metrics(raw)


def get_pending_candidates(tool_context: Any = None) -> Dict[str, Any]:
    """Return all stored candidate hypotheses for the Reviewer to rank.

    Reviewer batch ranks ALL pending candidates in a single LLM call,
    instead of reviewing one per loop iteration."""
    state = _state_from_context(tool_context)
    candidates = state.get("candidate_hypotheses") or []
    promoted = state.get("promoted_signals") or []
    promoted_names = {str(s.get("name") or "").strip() for s in promoted if isinstance(s, dict)}
    pending: List[Dict[str, Any]] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        name = str(cand.get("name") or "").strip()
        if name and name in promoted_names:
            continue
        if str(cand.get("status") or "").lower() == "promoted":
            continue
        pending.append(cand)
    objective = state.get("signal_objective") if isinstance(state.get("signal_objective"), dict) else {}
    return {
        "ok": True,
        "count": len(pending),
        "candidates": pending,
        "success_event_name": objective.get("success_event_name") or "",
    }


def propose_batch_candidates(
    candidates: List[Dict[str, Any]],
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Deterministically validate, execute, and store a batch of Explorer candidates.

    The Explorer emits a list of candidate drafts in a single LLM round; this
    tool runs the per-candidate side effects (dedupe → policy → query → store)
    without further LLM calls, collapsing what was previously ~6 round-trips
    per candidate into ~0 LLM rounds.

    Each candidate must include name/entity_grain/time_window/comparison_baseline/
    query_template/parameter_set/interpretation/target_event. SQL must SELECT
    columns named with the canonical cohort-evidence keys (success_cohort_size,
    failure_cohort_size, grey_cohort_size, conversion_lift,
    conversion_rate_delta, precision_proxy) for promotion to be possible.

    Returns one entry per candidate with status: stored | duplicate | policy_blocked
    | execution_failed | invalid.
    """
    state = _state_from_context(tool_context)
    try:
        if not isinstance(candidates, list) or not candidates:
            return {"ok": False, "error": "empty_batch", "results": []}

        # `SIGNAL_AGENT_BATCH_MAX_CANDIDATES` is a target told to the
        # Explorer's prompt — it now describes "candidates per propose_*
        # call", not "max Mason will keep". Process every candidate the
        # Explorer emits so the Reviewer always sees the full picture.
        # Hard ceiling stays at 50 to bound a runaway prompt; warn into
        # state if the Explorer exceeds the target by 2× so the next run
        # can investigate.
        target_per_call = _to_int_env("SIGNAL_AGENT_BATCH_MAX_CANDIDATES", 10)
        hard_ceiling = max(target_per_call * 3, 50)
        if len(candidates) > hard_ceiling:
            candidates = candidates[:hard_ceiling]
            state.setdefault("explorer_overflow_count", 0)
            state["explorer_overflow_count"] += 1

        sid = _session_id_arg(session_id, state)
        if not sid:
            return {"ok": False, "error": "missing_session_id"}

        results: List[Dict[str, Any]] = []
        stored = 0
        duplicates = 0
        policy_blocked_count = 0
        execution_failed = 0
        invalid_count = 0

        for raw in candidates:
            if not isinstance(raw, dict):
                invalid_count += 1
                results.append({
                    "ok": False,
                    "status": "invalid",
                    "error": "candidate_not_object",
                })
                continue
            ok_draft, validated_draft = _safe_model_validate(BatchedHypothesisDraft, raw)
            if not ok_draft:
                invalid_count += 1
                results.append({
                    "ok": False,
                    "status": "invalid",
                    "name": str(raw.get("name") or ""),
                    "error": "draft_validation_failed",
                    "detail": validated_draft.get("detail") if isinstance(validated_draft, dict) else "",
                })
                continue

            draft = validated_draft
            name = str(draft.get("name") or "").strip()
            template = str(draft.get("query_template") or "")
            params = draft.get("parameter_set") or {}
            rendered_sql = _render_template(template, params) if params else template
            unresolved = _find_unresolved_template_tokens(rendered_sql)

            # Dedupe against prior candidates by query_template + name.
            dedupe = dedupe_candidate(rendered_sql or template, tool_context=tool_context)
            if dedupe.get("is_duplicate"):
                duplicates += 1
                results.append({
                    "ok": False,
                    "status": "duplicate",
                    "name": name,
                    "duplicate_indexes": dedupe.get("duplicate_indexes", []),
                })
                continue

            if unresolved:
                invalid_count += 1
                results.append({
                    "ok": False,
                    "status": "invalid",
                    "name": name,
                    "error": "unresolved_template_parameters",
                    "unresolved_tokens": unresolved,
                })
                continue

            policy = validate_sql_policy(rendered_sql, tool_context=tool_context)
            if not policy.get("allowed"):
                policy_blocked_count += 1
                results.append({
                    "ok": False,
                    "status": "policy_blocked",
                    "name": name,
                    "rejection_class": policy.get("rejection_class", ""),
                    "violations": policy.get("violations", []),
                    "rewrite_hints": policy.get("rewrite_hints", []),
                })
                continue

            execution = run_readonly_query(
                sql=rendered_sql,
                purpose=f"batch_explorer:{name}",
                expected_grain=str(draft.get("entity_grain") or ""),
                session_id=sid,
                tool_context=tool_context,
            )
            if not execution.get("ok"):
                execution_failed += 1
                results.append({
                    "ok": False,
                    "status": "execution_failed",
                    "name": name,
                    "error": execution.get("error", ""),
                    "violations": execution.get("violations", []),
                    "detail": execution.get("detail", ""),
                })
                continue

            evidence = _extract_cohort_evidence(execution.get("columns"), execution.get("rows"))
            candidate_payload = {
                "name": name,
                "entity_grain": str(draft.get("entity_grain") or ""),
                "time_window": str(draft.get("time_window") or ""),
                "comparison_baseline": str(draft.get("comparison_baseline") or ""),
                "query_template": template,
                "parameter_set": params,
                "interpretation": str(draft.get("interpretation") or ""),
                "target_event": str(draft.get("target_event") or ""),
                "promotion_evidence": evidence,
                "execution_outcome": {
                    "status": "ok",
                    "purpose": f"batch_explorer:{name}",
                    "expected_grain": str(draft.get("entity_grain") or ""),
                    "sql": str(execution.get("query") or ""),
                    "row_count": int(execution.get("row_count") or 0),
                    "column_count": len(execution.get("columns") or []),
                    "cached": bool(execution.get("cached")),
                    "notes": "",
                },
                "status": "candidate",
            }
            store_result = store_candidate_signal(candidate_payload, tool_context=tool_context)
            if store_result.get("ok"):
                stored += 1
                results.append({
                    "ok": True,
                    "status": "stored",
                    "name": name,
                    "row_count": int(execution.get("row_count") or 0),
                    "has_cohort_evidence": bool(evidence),
                })
            else:
                invalid_count += 1
                results.append({
                    "ok": False,
                    "status": "invalid",
                    "name": name,
                    "error": store_result.get("error", "store_failed"),
                    "detail": store_result.get("detail", ""),
                })

        summary = {
            "ok": True,
            "batch_size": len(candidates),
            "stored": stored,
            "duplicates": duplicates,
            "policy_blocked": policy_blocked_count,
            "execution_failed": execution_failed,
            "invalid": invalid_count,
            "results": results,
        }
        state.setdefault("explorer_batch_summaries", []).append({
            "at": _utc_now(),
            **{k: v for k, v in summary.items() if k != "results"},
        })
        _record_artifact(state, "explorer_batch_summaries", summary, append=True)
        return summary
    except Exception as exc:
        return _tool_exception("propose_batch_candidates", exc, state)


def rice_prioritize_batch(
    rankings: List[Dict[str, Any]],
    tool_context: Any = None,
) -> Dict[str, Any]:
    """Apply Reviewer's RICE batch decisions to stored candidates.

    For every ranking with decision='promote', look up the candidate by
    name and call `store_candidate_signal` with promotion intent. Promotion
    still gates on `_candidate_mentions_success_target` and
    `_candidate_has_cohort_evidence` — RICE alone does not bypass those.

    Returns one entry per ranking with status: promoted | promote_blocked |
    skipped | not_found | invalid.
    """
    state = _state_from_context(tool_context)
    try:
        if not isinstance(rankings, list) or not rankings:
            return {"ok": False, "error": "empty_rankings", "results": []}
        ok_payload, payload = _safe_model_validate(
            RicePrioritizationPayload,
            {"rankings": rankings},
        )
        if not ok_payload:
            return {
                "ok": False,
                "error": "rankings_validation_failed",
                "detail": payload.get("detail", "") if isinstance(payload, dict) else "",
            }

        candidates = state.get("candidate_hypotheses") or []
        candidate_index = {
            str(c.get("name") or "").strip(): c
            for c in candidates
            if isinstance(c, dict) and (c.get("name"))
        }

        results: List[Dict[str, Any]] = []
        promoted_count = 0
        skipped = 0
        not_found = 0
        promote_blocked = 0
        invalid_count = 0

        for ranking in payload.get("rankings") or []:
            name = str(ranking.get("name") or "").strip()
            decision = str(ranking.get("decision") or "skip").strip().lower()
            if not name:
                invalid_count += 1
                results.append({"ok": False, "status": "invalid", "error": "missing_name"})
                continue

            if decision in ("skip", "defer", "drop", "reject"):
                skipped += 1
                review_payload = {
                    "decision": "reject",
                    "reasons": [str(ranking.get("rationale") or "rice_skip")],
                    "promotion_readiness": False,
                    "promoted": False,
                    "rice_score": float(ranking.get("rice_score") or 0.0),
                }
                cand = candidate_index.get(name)
                if cand is not None:
                    payload_for_store = dict(cand)
                    payload_for_store["review_decision"] = review_payload
                    store_candidate_signal(payload_for_store, tool_context=tool_context)
                results.append({
                    "ok": True,
                    "status": "skipped",
                    "name": name,
                    "rice_score": float(ranking.get("rice_score") or 0.0),
                })
                continue

            cand = candidate_index.get(name)
            if cand is None:
                not_found += 1
                results.append({
                    "ok": False,
                    "status": "not_found",
                    "name": name,
                })
                continue

            review_payload = {
                "decision": "approve",
                "reasons": [str(ranking.get("rationale") or "rice_promote")],
                "promotion_readiness": True,
                "promoted": True,
                "rice_score": float(ranking.get("rice_score") or 0.0),
                "reach": float(ranking.get("reach") or 0.0),
                "impact": float(ranking.get("impact") or 0.0),
                "confidence": float(ranking.get("confidence") or 0.0),
                "effort": float(ranking.get("effort") or 1.0),
            }
            promote_payload = dict(cand)
            promote_payload["promoted"] = True
            promote_payload["status"] = "promoted"
            promote_payload["review_decision"] = review_payload
            store_result = store_candidate_signal(promote_payload, tool_context=tool_context)
            if store_result.get("stored_as") == "promoted_signal":
                promoted_count += 1
                results.append({
                    "ok": True,
                    "status": "promoted",
                    "name": name,
                    "rice_score": float(ranking.get("rice_score") or 0.0),
                })
            else:
                promote_blocked += 1
                results.append({
                    "ok": False,
                    "status": "promote_blocked",
                    "name": name,
                    "reason": store_result.get("promotion_block_reason", "")
                              or store_result.get("error", "")
                              or "promotion_gate_failed",
                })

        summary = {
            "ok": True,
            "rankings_count": len(rankings),
            "promoted": promoted_count,
            "skipped": skipped,
            "not_found": not_found,
            "promote_blocked": promote_blocked,
            "invalid": invalid_count,
            "results": results,
        }
        state.setdefault("rice_batch_summaries", []).append({
            "at": _utc_now(),
            **{k: v for k, v in summary.items() if k != "results"},
        })
        _record_artifact(state, "rice_batch_summaries", summary, append=True)
        return summary
    except Exception as exc:
        return _tool_exception("rice_prioritize_batch", exc, state)


def _shift_param_dates(param_set: Dict[str, Any], shift_days: int) -> Dict[str, Any]:
    shifted = dict(param_set or {})
    for key, value in list(shifted.items()):
        if not isinstance(value, str):
            continue
        dt: Optional[datetime] = None
        out_format = ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            try:
                dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                out_format = "%Y-%m-%d"
            except Exception:
                dt = None
        elif re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", value):
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                out_format = "%Y-%m-%d %H:%M:%S"
            except Exception:
                dt = None
        if dt is None:
            continue
        shifted[key] = (dt + timedelta(days=shift_days)).strftime(out_format)
    return shifted


def _render_template(template: str, params: Dict[str, Any]) -> str:
    rendered = str(template or "")
    for key, value in (params or {}).items():
        replacement = str(value)
        rendered = rendered.replace(f"{{{{{key}}}}}", replacement)
        rendered = rendered.replace(f"{{{key}}}", replacement)
        rendered = rendered.replace(f"%({key})s", replacement)
    return rendered


def _find_unresolved_template_tokens(sql: str) -> List[str]:
    unresolved: List[str] = []
    for match in re.findall(r"%\(([a-zA-Z_][a-zA-Z0-9_]*)\)s", sql or ""):
        unresolved.append(match)
    for match in re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", sql or ""):
        unresolved.append(match)
    for match in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", sql or ""):
        unresolved.append(match)
    return sorted(set(unresolved))


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
        unresolved_tokens = _find_unresolved_template_tokens(sql)
        if unresolved_tokens:
            rerun_entry = {
                "name": signal.get("name"),
                "shift_days": int(holdout_shift_days),
                "params": shifted,
                "ok": False,
                "row_count": 0,
                "error": "unresolved_template_parameters",
                "unresolved_tokens": unresolved_tokens,
            }
            reruns.append(rerun_entry)
            _record_artifact(state, "holdout_rerun_results", rerun_entry, append=True)
            continue
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
        loop_iterations_observed = max(len(loop_events), len(candidates))
        fallback_stop_iteration = loop_iterations_observed
        fallback_stop_reason = "loop_exhausted_max_iterations" if loop_iterations_observed else "loop_not_started"
        llm_calls_used_estimate = int(state.get("llm_calls_used_estimate") or 0)
        if llm_calls_used_estimate <= 0 and loop_iterations_observed:
            # Each iteration runs explorer + reviewer, plus bootstrap/finalize overhead.
            llm_calls_used_estimate = (loop_iterations_observed * 2) + 2

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
        report_payload["stop_reason"] = str(last_loop_status.get("reason") or fallback_stop_reason)
        report_payload["stop_iteration"] = int(last_loop_status.get("iteration_index") or fallback_stop_iteration)
        report_payload["loop_iterations_observed"] = loop_iterations_observed
        report_payload["query_count_total"] = int(state.get("query_count") or 0)
        report_payload["llm_calls_used_estimate"] = llm_calls_used_estimate
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
