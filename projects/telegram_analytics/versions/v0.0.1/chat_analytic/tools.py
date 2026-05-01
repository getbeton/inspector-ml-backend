"""Database access and loop-state helpers for Telegram analytics experiments."""

import hashlib
import json
import os
import re
import uuid
from decimal import Decimal
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from .models import (
    AnalysisObjective,
    CandidateInsightDraft,
    DatabaseProfile,
    ExecutionOutcome,
    ExperimentReport,
    PromotedInsight,
    ReviewDecision,
)

if load_dotenv is not None:
    load_dotenv()

POLICY_VERSION = "v0.1.0"
DEFAULT_LOOKBACK_DAYS = 21
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
    "copy",
    "vacuum",
    "analyze",
    "refresh",
)
STATE_LIST_KEYS = (
    "candidate_hypotheses",
    "sql_attempts",
    "policy_validations",
    "review_decisions",
    "execution_summaries",
    "promoted_insights",
    "loop_iteration_events",
    "objective_evidence_snapshots",
)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _to_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _artifact_base_dir() -> Path:
    override = os.getenv("TELEGRAM_ANALYTICS_ARTIFACT_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    else:
        path = Path(__file__).resolve().parent / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_dir() -> Path:
    override = os.getenv("TELEGRAM_ANALYTICS_CACHE_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    else:
        path = Path(__file__).resolve().parent / ".cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key_to_path(cache_key: str) -> Path:
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return _cache_dir() / f"{digest}.json"


def cache_read_json(cache_key: str) -> Optional[Any]:
    path = _cache_key_to_path(cache_key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_write_json(cache_key: str, payload: Any) -> None:
    path = _cache_key_to_path(cache_key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _run_id_from_state(state: Dict[str, Any]) -> str:
    run_id = str(state.get("signal_run_id") or "").strip()
    if run_id:
        return run_id
    run_id = f"run_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    state["signal_run_id"] = run_id
    return run_id


def _run_dir(state: Dict[str, Any]) -> Path:
    path = _artifact_base_dir() / _run_id_from_state(state)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True))
        handle.write("\n")


def _state_from_context(tool_context: Any) -> Dict[str, Any]:
    if tool_context is None:
        return {}
    state = getattr(tool_context, "state", None)
    if state is None:
        return {}
    for key in STATE_LIST_KEYS:
        state.setdefault(key, [])
    state.setdefault("allowed_tables", [])
    state.setdefault("database_profile", {})
    state.setdefault("analysis_objective", {})
    state.setdefault("schema_snapshot", {})
    state.setdefault("tool_call_cache", {})
    state.setdefault("last_loop_status", {})
    state.setdefault("admin_candidate_ids", [])
    return state


def _record_artifact(state: Dict[str, Any], name: str, payload: Dict[str, Any], append: bool) -> None:
    if not state:
        return
    if append:
        _append_jsonl(_run_dir(state) / f"{name}.jsonl", payload)
    else:
        _write_json(_run_dir(state) / f"{name}.json", payload)


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
    result = cache.get(_tool_cache_key(tool_name, args_payload))
    if not isinstance(result, dict):
        return None
    payload = dict(result)
    payload["cached_call"] = True
    return payload


def _tool_cache_set(state: Dict[str, Any], tool_name: str, args_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
    cache = state.setdefault("tool_call_cache", {})
    if not isinstance(cache, dict):
        return
    cache[_tool_cache_key(tool_name, args_payload)] = dict(result)


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


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(val) for key, val in value.items()}
    return value


def _psycopg_module():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg is required for PostgreSQL access") from exc
    return psycopg


def _db_config() -> Dict[str, Any]:
    return {
        "host": os.getenv("TELEGRAM_DB_HOST", "127.0.0.1").strip() or "127.0.0.1",
        "port": int(os.getenv("TELEGRAM_DB_PORT", "5432")),
        "dbname": os.getenv("TELEGRAM_DB_NAME", "").strip(),
        "user": os.getenv("TELEGRAM_DB_USER", "").strip(),
        "password": os.getenv("TELEGRAM_DB_PASSWORD", "").strip(),
    }


def _db_signature() -> str:
    config = _db_config()
    safe = {key: value for key, value in config.items() if key != "password"}
    return json.dumps(safe, sort_keys=True, ensure_ascii=True)


def _ensure_db_config() -> None:
    config = _db_config()
    missing = [key for key in ("dbname", "user", "password") if not config.get(key)]
    if missing:
        raise RuntimeError(f"missing_database_env:{','.join(missing)}")


def _connect():
    _ensure_db_config()
    psycopg = _psycopg_module()
    return psycopg.connect(**_db_config())


def _fetch_rows(sql: str, params: Optional[Sequence[Any]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return [], []
            column_meta = [{"name": item.name, "type_code": str(item.type_code)} for item in cur.description]
            rows = []
            for raw_row in cur.fetchall():
                rows.append({item["name"]: _serialize_value(value) for item, value in zip(column_meta, raw_row)})
            return rows, column_meta


def _truncate_rows_for_llm(rows: List[Any]) -> List[Any]:
    max_rows = _to_int_env("TELEGRAM_ANALYTICS_LLM_ROW_RETURN_LIMIT", 12)
    return list(rows[: max(1, max_rows)])


def _safe_model_validate(model_cls: Any, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    try:
        validated = model_cls.model_validate(payload)
    except ValidationError as exc:
        return False, {"ok": False, "error": "validation_error", "detail": str(exc)}
    return True, validated.model_dump()


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = max(0.0, min(1.0, quantile)) * (len(sorted_values) - 1)
    lower_idx = int(position)
    upper_idx = min(len(sorted_values) - 1, lower_idx + 1)
    fraction = position - lower_idx
    lower = float(sorted_values[lower_idx])
    upper = float(sorted_values[upper_idx])
    return lower + (upper - lower) * fraction


def summarize_numeric_distribution(values: Sequence[Any], label: str = "") -> Dict[str, Any]:
    numeric_values = sorted(value for value in (_safe_float(item) for item in values) if value is not None)
    if not numeric_values:
        return {
            "ok": True,
            "label": label,
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
        }
    count = len(numeric_values)
    total = sum(numeric_values)
    return {
        "ok": True,
        "label": label,
        "count": count,
        "min": round(min(numeric_values), 4),
        "max": round(max(numeric_values), 4),
        "mean": round(total / count, 4),
        "median": round(_percentile(numeric_values, 0.5), 4),
        "p25": round(_percentile(numeric_values, 0.25), 4),
        "p75": round(_percentile(numeric_values, 0.75), 4),
        "p90": round(_percentile(numeric_values, 0.90), 4),
        "p95": round(_percentile(numeric_values, 0.95), 4),
        "p99": round(_percentile(numeric_values, 0.99), 4),
    }


def _session_id_arg(session_id: str, state: Dict[str, Any]) -> str:
    provided = (session_id or "").strip()
    if provided:
        state["session_id"] = provided
        return provided
    return str(state.get("session_id") or "").strip()


def _target_chat_arg(target_chat_uuid: str, state: Dict[str, Any]) -> str:
    provided = (target_chat_uuid or "").strip()
    if provided:
        state["target_chat_uuid"] = provided
        return provided
    return str(state.get("target_chat_uuid") or "").strip()


def initialize_signal_run(
    session_id: str = "",
    target_chat_uuid: str = "",
    max_iterations: int = 8,
    target_promoted_insights: int = 3,
    too_many_failures: int = 5,
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        args_payload = {
            "session_id": (session_id or "").strip(),
            "target_chat_uuid": (target_chat_uuid or "").strip(),
            "max_iterations": int(max_iterations),
            "target_promoted_insights": int(target_promoted_insights),
            "too_many_failures": int(too_many_failures),
        }
        cached = _tool_cache_get(state, "initialize_signal_run", args_payload)
        if cached is not None:
            return cached
        sid = _session_id_arg(session_id, state)
        chat_uuid = _target_chat_arg(target_chat_uuid, state)
        state["signal_run_id"] = f"run_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        for key in STATE_LIST_KEYS:
            state[key] = []
        state["allowed_tables"] = []
        state["schema_snapshot"] = {}
        state["database_profile"] = {}
        state["analysis_objective"] = {}
        state["query_count"] = 0
        state["failure_count"] = 0
        state["non_query_failure_count"] = 0
        state["iteration_index"] = 0
        state["last_loop_status"] = {}
        state["tool_call_cache"] = {}
        state["loop_limits"] = {
            "max_iterations": int(max_iterations),
            "target_promoted_insights": int(target_promoted_insights),
            "too_many_failures": int(too_many_failures),
        }
        payload = {
            "ok": True,
            "run_id": state["signal_run_id"],
            "session_id": sid,
            "target_chat_uuid": chat_uuid,
            "loop_limits": state["loop_limits"],
            "created_at": _utc_now(),
        }
        _record_artifact(state, "run_meta", payload, append=False)
        _tool_cache_set(state, "initialize_signal_run", args_payload, payload)
        return payload
    except Exception as exc:
        return _tool_exception("initialize_signal_run", exc, state)


def list_tables(session_id: str = "", max_tables: int = 120, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        sid = _session_id_arg(session_id, state)
        args_payload = {"session_id": sid, "max_tables": int(max_tables), "db": _db_signature()}
        cached = _tool_cache_get(state, "list_tables", args_payload)
        if cached is not None:
            return cached
        sql = """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
            LIMIT %s
        """
        rows, _ = _fetch_rows(sql, (max(1, int(max_tables)),))
        tables = [f"{row['table_schema']}.{row['table_name']}" for row in rows]
        state["allowed_tables"] = tables
        snapshot = {"captured_at": _utc_now(), "tables": rows, "table_names": tables}
        state["schema_snapshot"] = snapshot
        _record_artifact(state, "schema_snapshot", snapshot, append=False)
        result = {
            "ok": True,
            "session_id": sid,
            "table_count": len(tables),
            "tables": tables,
        }
        _tool_cache_set(state, "list_tables", args_payload, result)
        return result
    except Exception as exc:
        return _tool_exception("list_tables", exc, state)


def _split_table_name(table_name: str) -> Tuple[str, str]:
    cleaned = (table_name or "").strip().strip('"')
    if "." in cleaned:
        schema, table = cleaned.split(".", 1)
        return schema.strip('"'), table.strip('"')
    return "public", cleaned


def describe_table(table_name: str, session_id: str = "", sample_columns: int = 60, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        sid = _session_id_arg(session_id, state)
        schema_name, raw_table_name = _split_table_name(table_name)
        args_payload = {
            "session_id": sid,
            "table_name": f"{schema_name}.{raw_table_name}",
            "sample_columns": int(sample_columns),
            "db": _db_signature(),
        }
        cached = _tool_cache_get(state, "describe_table", args_payload)
        if cached is not None:
            return cached
        sql = """
            SELECT
                column_name,
                data_type,
                udt_name,
                is_nullable,
                ordinal_position
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            LIMIT %s
        """
        rows, _ = _fetch_rows(sql, (schema_name, raw_table_name, max(1, int(sample_columns))))
        schema_snapshot = state.get("schema_snapshot") or {}
        table_details = schema_snapshot.get("table_details")
        if not isinstance(table_details, dict):
            table_details = {}
        table_key = f"{schema_name}.{raw_table_name}"
        table_details[table_key] = {"columns": rows, "captured_at": _utc_now()}
        schema_snapshot["table_details"] = table_details
        state["schema_snapshot"] = schema_snapshot
        _record_artifact(state, "schema_snapshot", schema_snapshot, append=False)
        result = {
            "ok": True,
            "session_id": sid,
            "table_name": table_key,
            "column_count": len(rows),
            "columns": rows,
        }
        _tool_cache_set(state, "describe_table", args_payload, result)
        return result
    except Exception as exc:
        return _tool_exception("describe_table", exc, state)


def _normalize_sql_value(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip()).rstrip(";").strip()


def _extract_referenced_tables(sql: str) -> List[str]:
    tokens = re.findall(r"\b(?:from|join)\s+([a-zA-Z0-9_.\"]+)", sql, flags=re.IGNORECASE)
    tables = []
    for token in tokens:
        cleaned = token.strip().strip('"')
        if cleaned:
            tables.append(cleaned)
    return sorted(set(tables))


def _extract_cte_names(sql: str) -> List[str]:
    return sorted({name.lower() for name in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", sql, flags=re.IGNORECASE)})


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
    max_len = _to_int_env("TELEGRAM_ANALYTICS_SQL_MAX_LEN", 12000)
    max_rows = _to_int_env("TELEGRAM_ANALYTICS_MAX_ROWS", 500)
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
    for keyword in BLOCKED_SQL_PATTERNS:
        if re.search(rf"\b{re.escape(keyword)}\b", lower):
            violations.append(f"blocked_keyword:{keyword}")

    referenced_tables = _extract_referenced_tables(normalized_sql)
    cte_names = set(_extract_cte_names(normalized_sql))
    allowed_tables = set(state.get("allowed_tables") or [])
    if allowed_tables:
        unknown_tables = [
            table
            for table in referenced_tables
            if table not in allowed_tables and table.lower() not in cte_names and "." in table
        ]
        if unknown_tables:
            violations.append("unknown_tables_referenced")
    else:
        unknown_tables = []

    if any(item.startswith("information_schema.") or item.startswith("pg_catalog.") for item in referenced_tables):
        violations.append("system_catalog_access_not_allowed")

    enforced_sql, limit_applied, existing_limit = _enforce_limit(normalized_sql, max_rows)
    allowed = not violations
    payload = {
        "checked_at": _utc_now(),
        "policy_version": POLICY_VERSION,
        "allowed": allowed,
        "violations": sorted(set(violations)),
        "normalized_sql": normalized_sql,
        "enforced_sql": enforced_sql,
        "max_query_length": max_len,
        "max_rows": max_rows,
        "limit_applied": limit_applied,
        "existing_limit": existing_limit,
        "referenced_tables": referenced_tables,
        "unknown_tables": unknown_tables,
    }
    state.setdefault("policy_validations", []).append(payload)
    _record_artifact(state, "policy_validations", payload, append=True)
    return payload


def validate_chat_scope(sql: str, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    target_chat_uuid = str(state.get("target_chat_uuid") or "")
    objective = state.get("analysis_objective") or {}
    profile = state.get("database_profile") or {}
    chat_column_candidates = [
        str(profile.get("inferred_message_chat_column") or ""),
        str(profile.get("inferred_reaction_chat_column") or ""),
        str(objective.get("schema_hints", {}).get("chat_column") or ""),
        "chat_id",
        "chat_uuid",
        "group_id",
        "group_uuid",
    ]
    compact = (sql or "").lower()
    violations: List[str] = []
    if not target_chat_uuid:
        return {"ok": True, "allowed": True, "violations": []}
    has_chat_uuid = target_chat_uuid.lower() in compact or "{chat_uuid}" in compact or "%(chat_uuid)s" in compact
    has_chat_column = any(candidate and candidate.lower() in compact for candidate in chat_column_candidates)
    if not has_chat_uuid:
        violations.append("missing_target_chat_uuid_filter")
    if not has_chat_column:
        violations.append("missing_chat_column_reference")
    return {
        "ok": True,
        "allowed": not violations,
        "violations": violations,
        "target_chat_uuid": target_chat_uuid,
    }


def _scope_validation_required(purpose: str) -> bool:
    lower = (purpose or "").strip().lower()
    if not lower:
        return True
    allowed_prefixes = (
        "sample_rows:",
        "profile_chat_activity",
        "identify_admin_candidates",
        "profile_user_return_gaps",
        "profile_first_to_next_activity",
        "profile_activity_gap_distribution",
        "profile_activity_frequency_distribution",
    )
    return not any(lower.startswith(prefix) for prefix in allowed_prefixes)


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
        max_queries = _to_int_env("TELEGRAM_ANALYTICS_MAX_QUERIES", 30)
        query_count = int(state.get("query_count") or 0)
        if query_count >= max_queries:
            outcome = {"ok": False, "error": "query_budget_exceeded", "max_queries": max_queries, "at": _utc_now()}
            state.setdefault("execution_summaries", []).append(outcome)
            _record_artifact(state, "execution_summaries", outcome, append=True)
            return outcome

        scope = validate_chat_scope(sql, tool_context=tool_context)
        if _scope_validation_required(purpose) and not scope.get("allowed"):
            state["failure_count"] = int(state.get("failure_count") or 0) + 1
            return {"ok": False, "error": "chat_scope_blocked", "violations": scope.get("violations", [])}
        policy = validate_sql_policy(sql, tool_context=tool_context)
        sql_attempt = {
            "attempted_at": _utc_now(),
            "session_id": sid,
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
                "violations": policy.get("violations", []),
                "unknown_tables": policy.get("unknown_tables", []),
            }

        enforced_sql = str(policy.get("enforced_sql") or "")
        cache_key = f"query:v1:{_db_signature()}:{enforced_sql}"
        cached = cache_read_json(cache_key)
        if isinstance(cached, dict):
            rows = cached.get("rows", [])
            columns = cached.get("columns", [])
            cached_hit = True
        else:
            rows, columns = _fetch_rows(enforced_sql)
            cache_write_json(cache_key, {"rows": rows, "columns": columns})
            cached_hit = False
        state["query_count"] = query_count + 1
        llm_rows = _truncate_rows_for_llm(rows)
        outcome = ExecutionOutcome(
            status="ok",
            purpose=purpose or "readonly_query",
            expected_grain=expected_grain,
            sql=enforced_sql,
            row_count=len(rows),
            column_count=len(columns),
            cached=cached_hit,
            notes="",
        ).model_dump()
        state.setdefault("execution_summaries", []).append(outcome)
        _record_artifact(state, "execution_summaries", outcome, append=True)
        return {
            "ok": True,
            "session_id": sid,
            "query": enforced_sql,
            "purpose": purpose,
            "expected_grain": expected_grain,
            "columns": columns,
            "rows": llm_rows,
            "row_count": len(rows),
            "returned_row_count": len(llm_rows),
            "cached": cached_hit,
            "policy_version": POLICY_VERSION,
        }
    except Exception as exc:
        return _tool_exception("run_readonly_query", exc, state)


def sample_rows(table_name: str, session_id: str = "", limit: int = 5, tool_context: Any = None) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit), _to_int_env("TELEGRAM_ANALYTICS_MAX_ROWS", 500)))
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


def _guess_table(profile_kind: str, tables: Sequence[str]) -> str:
    scores: List[Tuple[int, str]] = []
    for table in tables:
        lower = table.lower()
        score = 0
        if profile_kind == "message":
            if "message" in lower:
                score += 5
            if "chat" in lower:
                score += 1
        elif profile_kind == "reaction":
            if "reaction" in lower:
                score += 5
            if "emoji" in lower:
                score += 2
        elif profile_kind == "membership":
            if "member" in lower:
                score += 5
            if "participant" in lower or "join" in lower:
                score += 2
        elif profile_kind == "chat":
            if lower.endswith(".chats") or lower.endswith(".groups"):
                score += 5
            if "chat" in lower or "group" in lower:
                score += 2
        elif profile_kind == "user":
            if lower.endswith(".users"):
                score += 5
            if "user" in lower or "member" in lower:
                score += 2
        scores.append((score, table))
    scores.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scores[0][1] if scores and scores[0][0] > 0 else ""


def _guess_column(columns: Sequence[str], keywords: Sequence[str]) -> str:
    scored: List[Tuple[int, str]] = []
    for column in columns:
        lower = column.lower()
        score = 0
        for idx, keyword in enumerate(keywords):
            if lower == keyword:
                score += 10 - idx
            elif keyword in lower:
                score += 5 - min(idx, 4)
        scored.append((score, column))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][1] if scored and scores_above_zero(scored) else ""


def scores_above_zero(scored: Sequence[Tuple[int, str]]) -> bool:
    return bool(scored and scored[0][0] > 0)


def _columns_from_snapshot(state: Dict[str, Any], table_name: str) -> List[str]:
    snapshot = state.get("schema_snapshot") or {}
    details = snapshot.get("table_details") or {}
    table_meta = details.get(table_name) or {}
    columns = table_meta.get("columns") or []
    return [str(item.get("column_name") or "") for item in columns if item.get("column_name")]


def profile_chat_activity(
    message_table: str,
    chat_column: str,
    time_column: str,
    target_chat_uuid: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    chat_uuid = _target_chat_arg(target_chat_uuid, state)
    sql = (
        f"SELECT date_trunc('day', {time_column}) AS activity_day, COUNT(*) AS message_count "
        f"FROM {message_table} "
        f"WHERE {chat_column} = '{chat_uuid}' "
        f"GROUP BY 1 ORDER BY 1 DESC LIMIT 30"
    )
    return run_readonly_query(
        sql=sql,
        purpose="profile_chat_activity",
        expected_grain="chat_day",
        session_id=session_id,
        tool_context=tool_context,
    )


def identify_admin_candidates(
    message_table: str,
    chat_column: str,
    user_column: str,
    target_chat_uuid: str = "",
    top_k: int = 3,
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    chat_uuid = _target_chat_arg(target_chat_uuid, state)
    sql = (
        f"SELECT {user_column} AS user_id, COUNT(*) AS message_count "
        f"FROM {message_table} "
        f"WHERE {chat_column} = '{chat_uuid}' "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT {max(1, int(top_k))}"
    )
    result = run_readonly_query(
        sql=sql,
        purpose="identify_admin_candidates",
        expected_grain="user",
        session_id=session_id,
        tool_context=tool_context,
    )
    if result.get("ok"):
        rows = result.get("rows") or []
        candidate_ids = [str(row.get("user_id")) for row in rows if row.get("user_id") is not None]
        state["admin_candidate_ids"] = candidate_ids
        result["admin_candidate_ids"] = candidate_ids
        result["heuristic"] = "top_message_sender"
    return result


def _admin_exclusion_clause(user_column: str, state: Dict[str, Any]) -> str:
    admin_candidate_ids = [str(item) for item in state.get("admin_candidate_ids") or [] if str(item).strip()]
    if not admin_candidate_ids:
        return ""
    quoted = ", ".join("'" + item.replace("'", "''") + "'" for item in admin_candidate_ids)
    return f" AND {user_column} NOT IN ({quoted})"


def profile_user_return_gaps(
    message_table: str,
    chat_column: str,
    user_column: str,
    time_column: str,
    target_chat_uuid: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    chat_uuid = _target_chat_arg(target_chat_uuid, state)
    exclusion = _admin_exclusion_clause(user_column, state)
    sql = f"""
        WITH ordered_activity AS (
            SELECT
                {user_column} AS user_id,
                {time_column} AS activity_at,
                LAG({time_column}) OVER (PARTITION BY {user_column} ORDER BY {time_column}) AS previous_activity_at
            FROM {message_table}
            WHERE {chat_column} = '{chat_uuid}'{exclusion}
        )
        SELECT
            EXTRACT(EPOCH FROM (activity_at - previous_activity_at)) / 86400.0 AS gap_days
        FROM ordered_activity
        WHERE previous_activity_at IS NOT NULL
        ORDER BY gap_days
        LIMIT 500
    """
    result = run_readonly_query(
        sql=sql,
        purpose="profile_user_return_gaps",
        expected_grain="user_gap",
        session_id=session_id,
        tool_context=tool_context,
    )
    if not result.get("ok"):
        return result
    values = [row.get("gap_days") for row in result.get("rows") or []]
    distribution = summarize_numeric_distribution(values, label="user_return_gap_days")
    payload = {
        "ok": True,
        "metric_name": "user_return_gap_days",
        "unit": "days",
        "sample_size": distribution.get("count", 0),
        "distribution": distribution,
        "rows": result.get("rows", []),
    }
    state["latest_user_return_gap_profile"] = payload
    _record_artifact(state, "latest_user_return_gap_profile", payload, append=False)
    return payload


def profile_first_to_next_activity(
    message_table: str,
    chat_column: str,
    user_column: str,
    time_column: str,
    target_chat_uuid: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    chat_uuid = _target_chat_arg(target_chat_uuid, state)
    exclusion = _admin_exclusion_clause(user_column, state)
    sql = f"""
        WITH ordered_activity AS (
            SELECT
                {user_column} AS user_id,
                {time_column} AS activity_at,
                ROW_NUMBER() OVER (PARTITION BY {user_column} ORDER BY {time_column}) AS activity_rank
            FROM {message_table}
            WHERE {chat_column} = '{chat_uuid}'{exclusion}
        ),
        first_activity AS (
            SELECT user_id, activity_at AS first_activity_at
            FROM ordered_activity
            WHERE activity_rank = 1
        ),
        next_activity AS (
            SELECT user_id, activity_at AS next_activity_at
            FROM ordered_activity
            WHERE activity_rank = 2
        )
        SELECT
            EXTRACT(EPOCH FROM (n.next_activity_at - f.first_activity_at)) / 86400.0 AS days_to_next_activity
        FROM first_activity f
        INNER JOIN next_activity n ON n.user_id = f.user_id
        ORDER BY days_to_next_activity
        LIMIT 500
    """
    result = run_readonly_query(
        sql=sql,
        purpose="profile_first_to_next_activity",
        expected_grain="user_gap",
        session_id=session_id,
        tool_context=tool_context,
    )
    if not result.get("ok"):
        return result
    values = [row.get("days_to_next_activity") for row in result.get("rows") or []]
    distribution = summarize_numeric_distribution(values, label="days_to_next_activity")
    payload = {
        "ok": True,
        "metric_name": "days_to_next_activity",
        "unit": "days",
        "sample_size": distribution.get("count", 0),
        "distribution": distribution,
        "rows": result.get("rows", []),
    }
    state["latest_first_to_next_activity_profile"] = payload
    _record_artifact(state, "latest_first_to_next_activity_profile", payload, append=False)
    return payload


def profile_activity_gap_distribution(
    metric_name: str = "user_return_gap_days",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    source = {}
    if metric_name == "days_to_next_activity":
        source = state.get("latest_first_to_next_activity_profile") or {}
    else:
        source = state.get("latest_user_return_gap_profile") or {}
    distribution = source.get("distribution") if isinstance(source, dict) else {}
    payload = {
        "ok": True,
        "metric_name": metric_name,
        "distribution": distribution or summarize_numeric_distribution([], label=metric_name),
        "sample_size": int((distribution or {}).get("count") or 0),
    }
    return payload


def profile_activity_frequency_distribution(
    message_table: str,
    chat_column: str,
    user_column: str,
    target_chat_uuid: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    chat_uuid = _target_chat_arg(target_chat_uuid, state)
    exclusion = _admin_exclusion_clause(user_column, state)
    sql = f"""
        SELECT
            message_count
        FROM (
            SELECT {user_column} AS user_id, COUNT(*) AS message_count
            FROM {message_table}
            WHERE {chat_column} = '{chat_uuid}'{exclusion}
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 500
        ) counted
        ORDER BY message_count
    """
    result = run_readonly_query(
        sql=sql,
        purpose="profile_activity_frequency_distribution",
        expected_grain="user_frequency",
        session_id=session_id,
        tool_context=tool_context,
    )
    if not result.get("ok"):
        return result
    values = [row.get("message_count") for row in result.get("rows") or []]
    distribution = summarize_numeric_distribution(values, label="message_count_per_user")
    payload = {
        "ok": True,
        "metric_name": "message_count_per_user",
        "unit": "messages",
        "sample_size": distribution.get("count", 0),
        "distribution": distribution,
        "rows": result.get("rows", []),
    }
    state["latest_activity_frequency_profile"] = payload
    _record_artifact(state, "latest_activity_frequency_profile", payload, append=False)
    return payload


def store_objective_evidence(
    objective_evidence: Dict[str, Any],
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        payload = dict(objective_evidence or {})
        payload.setdefault("captured_at", _utc_now())
        state.setdefault("objective_evidence_snapshots", []).append(payload)
        state["latest_objective_evidence"] = payload
        _record_artifact(state, "objective_evidence_snapshots", payload, append=True)
        return {"ok": True, "objective_evidence": payload}
    except Exception as exc:
        return _tool_exception("store_objective_evidence", exc, state)


def profile_retention_windows(
    message_table: str,
    chat_column: str,
    user_column: str,
    time_column: str,
    target_chat_uuid: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    chat_uuid = _target_chat_arg(target_chat_uuid, state)
    sql = f"""
        WITH first_seen AS (
            SELECT
                {user_column} AS user_id,
                MIN({time_column}) AS first_activity_at
            FROM {message_table}
            WHERE {chat_column} = '{chat_uuid}'
            GROUP BY 1
        ),
        followups AS (
            SELECT
                f.user_id,
                MAX(CASE WHEN m.{time_column} >= f.first_activity_at + INTERVAL '1 day'
                          AND m.{time_column} < f.first_activity_at + INTERVAL '2 day' THEN 1 ELSE 0 END) AS retained_d1,
                MAX(CASE WHEN m.{time_column} >= f.first_activity_at + INTERVAL '7 day'
                          AND m.{time_column} < f.first_activity_at + INTERVAL '8 day' THEN 1 ELSE 0 END) AS retained_d7,
                MAX(CASE WHEN m.{time_column} >= f.first_activity_at + INTERVAL '14 day'
                          AND m.{time_column} < f.first_activity_at + INTERVAL '15 day' THEN 1 ELSE 0 END) AS retained_d14,
                MAX(CASE WHEN m.{time_column} >= f.first_activity_at + INTERVAL '21 day'
                          AND m.{time_column} < f.first_activity_at + INTERVAL '22 day' THEN 1 ELSE 0 END) AS retained_d21
            FROM first_seen f
            LEFT JOIN {message_table} m
              ON m.{user_column} = f.user_id
             AND m.{chat_column} = '{chat_uuid}'
            GROUP BY 1
        )
        SELECT
            COUNT(*) AS users,
            AVG(retained_d1::float) AS retention_d1,
            AVG(retained_d7::float) AS retention_d7,
            AVG(retained_d14::float) AS retention_d14,
            AVG(retained_d21::float) AS retention_d21
        FROM followups
    """
    return run_readonly_query(
        sql=sql,
        purpose="profile_retention_windows",
        expected_grain="cohort_summary",
        session_id=session_id,
        tool_context=tool_context,
    )


def profile_reaction_reply_effects(
    message_table: str,
    chat_column: str,
    user_column: str,
    message_id_column: str,
    time_column: str,
    reaction_table: str = "",
    reaction_message_id_column: str = "",
    reaction_time_column: str = "",
    reaction_chat_column: str = "",
    reply_to_column: str = "",
    target_chat_uuid: str = "",
    session_id: str = "",
    tool_context: Any = None,
) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    chat_uuid = _target_chat_arg(target_chat_uuid, state)
    reaction_cte = ""
    reaction_join = ""
    if reaction_table and reaction_message_id_column and reaction_time_column:
        reaction_cte = f"""
        , reaction_events AS (
            SELECT
                {reaction_message_id_column} AS reacted_message_id,
                MIN({reaction_time_column}) AS first_reaction_at
            FROM {reaction_table}
            WHERE {(reaction_chat_column or chat_column)} = '{chat_uuid}'
            GROUP BY 1
        )
        """
        reaction_join = """
            LEFT JOIN reaction_events r
              ON r.reacted_message_id = f.first_message_id
        """
    reply_cte = ""
    reply_join = ""
    if reply_to_column:
        reply_cte = f"""
        , reply_events AS (
            SELECT
                {reply_to_column} AS replied_message_id,
                MIN({time_column}) AS first_reply_at
            FROM {message_table}
            WHERE {chat_column} = '{chat_uuid}' AND {reply_to_column} IS NOT NULL
            GROUP BY 1
        )
        """
        reply_join = """
            LEFT JOIN reply_events rp
              ON rp.replied_message_id = f.first_message_id
        """
    sql = f"""
        WITH first_messages AS (
            SELECT DISTINCT ON ({user_column})
                {user_column} AS user_id,
                {message_id_column} AS first_message_id,
                {time_column} AS first_message_at
            FROM {message_table}
            WHERE {chat_column} = '{chat_uuid}'
            ORDER BY {user_column}, {time_column}
        )
        {reaction_cte}
        {reply_cte}
        , future_activity AS (
            SELECT
                f.user_id,
                COUNT(*) FILTER (
                    WHERE m.{time_column} > f.first_message_at
                      AND m.{time_column} <= f.first_message_at + INTERVAL '21 day'
                ) AS future_messages_21d
            FROM first_messages f
            LEFT JOIN {message_table} m
              ON m.{user_column} = f.user_id
             AND m.{chat_column} = '{chat_uuid}'
            GROUP BY 1
        )
        SELECT
            COUNT(*) AS users,
            AVG(CASE WHEN r.first_reaction_at IS NOT NULL
                      AND r.first_reaction_at <= f.first_message_at + INTERVAL '10 minute'
                     THEN 1 ELSE 0 END)::float AS reaction_in_10m_rate,
            AVG(CASE WHEN rp.first_reply_at IS NOT NULL
                      AND rp.first_reply_at <= f.first_message_at + INTERVAL '10 minute'
                     THEN 1 ELSE 0 END)::float AS reply_in_10m_rate,
            AVG(CASE WHEN fa.future_messages_21d > 0 THEN 1 ELSE 0 END)::float AS retained_any_21d,
            AVG(fa.future_messages_21d::float) AS avg_future_messages_21d
        FROM first_messages f
        LEFT JOIN future_activity fa ON fa.user_id = f.user_id
        {reaction_join}
        {reply_join}
    """
    return run_readonly_query(
        sql=sql,
        purpose="profile_reaction_reply_effects",
        expected_grain="cohort_summary",
        session_id=session_id,
        tool_context=tool_context,
    )


def _select_retention_windows(failure_days: int) -> List[int]:
    base = sorted({1, max(1, failure_days // 3), max(1, failure_days // 2), max(1, failure_days)})
    return [int(item) for item in base[:4]]


def _objective_evidence_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    latest_evidence = state.get("latest_objective_evidence")
    if isinstance(latest_evidence, dict) and latest_evidence:
        return latest_evidence
    return {
        "gap_profile": state.get("latest_user_return_gap_profile") or {},
        "first_to_next_profile": state.get("latest_first_to_next_activity_profile") or {},
        "frequency_profile": state.get("latest_activity_frequency_profile") or {},
        "admin_candidate_ids": state.get("admin_candidate_ids") or [],
    }


def infer_analysis_objective(lookback_days: int = DEFAULT_LOOKBACK_DAYS, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        profile = state.get("database_profile") or {}
        if not isinstance(profile, dict):
            profile = {}
        target_chat_uuid = str(state.get("target_chat_uuid") or "")
        admin_candidate_ids = [str(item) for item in state.get("admin_candidate_ids") or []]
        evidence = _objective_evidence_from_state(state)
        gap_distribution = ((evidence.get("gap_profile") or {}).get("distribution") or {})
        next_distribution = ((evidence.get("first_to_next_profile") or {}).get("distribution") or {})
        frequency_distribution = ((evidence.get("frequency_profile") or {}).get("distribution") or {})

        candidate_failure_days = _safe_float(gap_distribution.get("p90"))
        fallback_failure_days = _safe_float(gap_distribution.get("p75"))
        if candidate_failure_days is None:
            candidate_failure_days = fallback_failure_days
        if candidate_failure_days is None:
            candidate_failure_days = float(max(1, int(lookback_days)))
        candidate_failure_days = max(1.0, min(candidate_failure_days, 60.0))
        failure_days_int = int(round(candidate_failure_days))

        candidate_success_days = _safe_float(next_distribution.get("p75"))
        if candidate_success_days is None:
            candidate_success_days = min(float(failure_days_int), 7.0)
        candidate_success_days = max(0.0417, min(candidate_success_days, float(failure_days_int)))

        candidate_success_frequency = _safe_float(frequency_distribution.get("p75"))
        if candidate_success_frequency is None:
            candidate_success_frequency = 2.0
        candidate_success_frequency = max(2.0, candidate_success_frequency)

        evidence_notes: List[str] = [
            "Bootstrap inferred objective thresholds from observed target-chat activity distributions.",
            f"Failure threshold candidate used p90 of inactivity gaps when available: {gap_distribution.get('p90')}.",
            f"Success return threshold candidate used p75 of first-to-next activity gaps when available: {next_distribution.get('p75')}.",
            f"Success frequency candidate used p75 of per-user message counts when available: {frequency_distribution.get('p75')}.",
        ]
        fallback_used = gap_distribution.get("count", 0) == 0 or next_distribution.get("count", 0) == 0
        if fallback_used:
            evidence_notes.append("Sparse bootstrap evidence required fallback defaults for one or more thresholds.")

        objective_payload = {
            "session_id": str(state.get("session_id") or ""),
            "target_chat_uuid": target_chat_uuid,
            "qualifying_activity": ["message", "reply", "reaction"],
            "success_definition": (
                f"A user is considered successful when they return to the target chat within {round(candidate_success_days, 2)} days "
                f"of earlier activity or achieve at least {int(round(candidate_success_frequency))} qualifying activities in the chat."
            ),
            "failure_definition": f"A user is considered failed or dropped off when they show no qualifying activity in the target chat for {failure_days_int} days.",
            "success_metric": "days_to_next_activity_or_message_count_per_user",
            "failure_metric": "user_return_gap_days",
            "success_threshold_value": round(candidate_success_days, 4),
            "failure_threshold_value": float(failure_days_int),
            "threshold_unit": "days",
            "admin_exclusion_strategy": "Prefer explicit admin role columns; otherwise exclude top message sender heuristic.",
            "admin_candidate_ids": admin_candidate_ids,
            "lookback_days": failure_days_int,
            "retention_windows_days": _select_retention_windows(failure_days_int),
            "objective_confidence": 0.45 if fallback_used else 0.78,
            "objective_evidence": {
                "gap_profile": evidence.get("gap_profile") or {},
                "first_to_next_profile": evidence.get("first_to_next_profile") or {},
                "frequency_profile": evidence.get("frequency_profile") or {},
                "selected_thresholds": {
                    "failure_days_from_gap_p90": float(failure_days_int),
                    "success_days_from_next_activity_p75": round(candidate_success_days, 4),
                    "success_frequency_from_message_count_p75": round(candidate_success_frequency, 4),
                },
                "rejected_thresholds": {
                    "failure_days_from_gap_p75": fallback_failure_days,
                    "static_21_day_failure_rule": 21,
                },
            },
            "schema_hints": {
                "chat_table": str(profile.get("inferred_chat_table") or ""),
                "message_table": str(profile.get("inferred_message_table") or ""),
                "reaction_table": str(profile.get("inferred_reaction_table") or ""),
                "membership_table": str(profile.get("inferred_membership_table") or ""),
                "user_table": str(profile.get("inferred_user_table") or ""),
                "chat_column": str(profile.get("inferred_message_chat_column") or ""),
                "message_time_column": str(profile.get("inferred_message_time_column") or ""),
                "message_user_column": str(profile.get("inferred_message_user_column") or ""),
                "message_id_column": str(profile.get("inferred_message_id_column") or ""),
                "reply_to_column": str(profile.get("inferred_reply_to_column") or ""),
            },
            "objective_notes": [
                "All insights must remain scoped to the target chat UUID.",
                "Bootstrap may inspect whole-schema metadata, but hypothesis testing is single-chat only.",
                *evidence_notes,
            ],
            "captured_at": _utc_now(),
        }
        ok, validated = _safe_model_validate(AnalysisObjective, objective_payload)
        if not ok:
            return validated
        state["analysis_objective"] = validated
        _record_artifact(state, "analysis_objective", validated, append=False)
        return {"ok": True, "analysis_objective": validated}
    except Exception as exc:
        return _tool_exception("infer_analysis_objective", exc, state)


def get_analysis_objective(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    objective = state.get("analysis_objective")
    if isinstance(objective, dict) and objective.get("target_chat_uuid"):
        return {"ok": True, "analysis_objective": objective}
    return infer_analysis_objective(tool_context=tool_context)


def store_database_profile(profile: Dict[str, Any], tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        payload = dict(profile or {})
        if not payload.get("available_tables"):
            payload["available_tables"] = list(state.get("allowed_tables") or [])
        payload.setdefault("session_id", str(state.get("session_id") or ""))
        payload.setdefault("target_chat_uuid", str(state.get("target_chat_uuid") or ""))
        available_tables = payload.get("available_tables") or []
        if not payload.get("inferred_message_table"):
            payload["inferred_message_table"] = _guess_table("message", available_tables)
        if not payload.get("inferred_reaction_table"):
            payload["inferred_reaction_table"] = _guess_table("reaction", available_tables)
        if not payload.get("inferred_membership_table"):
            payload["inferred_membership_table"] = _guess_table("membership", available_tables)
        if not payload.get("inferred_chat_table"):
            payload["inferred_chat_table"] = _guess_table("chat", available_tables)
        if not payload.get("inferred_user_table"):
            payload["inferred_user_table"] = _guess_table("user", available_tables)

        message_columns = _columns_from_snapshot(state, str(payload.get("inferred_message_table") or ""))
        reaction_columns = _columns_from_snapshot(state, str(payload.get("inferred_reaction_table") or ""))
        membership_columns = _columns_from_snapshot(state, str(payload.get("inferred_membership_table") or ""))
        if message_columns:
            payload.setdefault("inferred_message_time_column", _guess_column(message_columns, ["created_at", "sent_at", "timestamp", "message_date"]))
            payload.setdefault("inferred_message_chat_column", _guess_column(message_columns, ["chat_uuid", "chat_id", "group_uuid", "group_id"]))
            payload.setdefault("inferred_message_user_column", _guess_column(message_columns, ["user_uuid", "user_id", "sender_id", "author_id"]))
            payload.setdefault("inferred_message_id_column", _guess_column(message_columns, ["message_uuid", "message_id", "id"]))
            payload.setdefault("inferred_reply_to_column", _guess_column(message_columns, ["reply_to_message_id", "reply_to_id", "parent_message_id"]))
        if reaction_columns:
            payload.setdefault("inferred_reaction_time_column", _guess_column(reaction_columns, ["created_at", "reacted_at", "timestamp"]))
            payload.setdefault("inferred_reaction_chat_column", _guess_column(reaction_columns, ["chat_uuid", "chat_id", "group_uuid", "group_id"]))
            payload.setdefault("inferred_reaction_user_column", _guess_column(reaction_columns, ["user_uuid", "user_id", "sender_id", "author_id"]))
        if membership_columns:
            payload.setdefault("inferred_membership_joined_at_column", _guess_column(membership_columns, ["joined_at", "created_at", "member_since"]))
            payload.setdefault("inferred_membership_left_at_column", _guess_column(membership_columns, ["left_at", "removed_at", "deleted_at"]))
        payload.setdefault("inferred_admin_source", "top_message_sender_heuristic")
        ok, validated = _safe_model_validate(DatabaseProfile, payload)
        if not ok:
            return validated
        state["database_profile"] = validated
        _record_artifact(state, "database_profile", validated, append=False)
        return {"ok": True, "database_profile": validated}
    except Exception as exc:
        return _tool_exception("store_database_profile", exc, state)


def get_database_profile(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    profile = state.get("database_profile") or {}
    return {"ok": True, "database_profile": profile}


def load_existing_insights(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    return {
        "ok": True,
        "candidate_hypotheses": state.get("candidate_hypotheses") or [],
        "review_decisions": state.get("review_decisions") or [],
        "promoted_insights": state.get("promoted_insights") or [],
    }


def dedupe_candidate(sql_or_semantics: str, tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    normalized = _normalize_sql_value(sql_or_semantics)
    seen = []
    for item in state.get("candidate_hypotheses") or []:
        if not isinstance(item, dict):
            continue
        seen.extend(
            [
                _normalize_sql_value(str(item.get("query_template") or "")),
                _normalize_sql_value(str(item.get("interpretation") or "")),
                _normalize_sql_value(str(item.get("name") or "")),
                _normalize_sql_value(str(item.get("hypothesis") or "")),
            ]
        )
    return {"ok": True, "duplicate": normalized in set(seen), "normalized_value": normalized}


def _normalize_review_decision(review_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(review_payload or {})
    decision_raw = str(payload.get("decision") or payload.get("status") or "").strip().lower()
    promoted_flag = bool(payload.get("promoted"))
    approve_tokens = {"approve", "approved", "promote", "promoted", "accept", "accepted", "pass", "passed"}
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    fixes = payload.get("required_fixes") if isinstance(payload.get("required_fixes"), list) else []
    return {
        "decision": "approve" if decision_raw in approve_tokens or promoted_flag else "reject",
        "reasons": [str(item).strip() for item in reasons if str(item).strip()],
        "required_fixes": [str(item).strip() for item in fixes if str(item).strip()],
        "promotion_readiness": bool(payload.get("promotion_readiness")) or promoted_flag or decision_raw in approve_tokens,
        "promoted": promoted_flag,
    }


def store_candidate_insight(candidate: Dict[str, Any], tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        payload = dict(candidate or {})
        review_payload = payload.pop("review_decision", None)
        execution_payload = payload.pop("execution_outcome", None)
        args_payload = {"candidate": payload, "review_decision": review_payload, "execution_outcome": execution_payload}
        cached = _tool_cache_get(state, "store_candidate_insight", args_payload)
        if cached is not None:
            return cached
        ok, validated = _safe_model_validate(CandidateInsightDraft, payload)
        if not ok:
            return validated
        existing_candidates = state.setdefault("candidate_hypotheses", [])
        normalized_identity = (
            _normalize_sql_value(str(validated.get("query_template") or "")),
            _normalize_sql_value(str(validated.get("name") or "")),
            _normalize_sql_value(str(validated.get("hypothesis") or "")),
        )
        duplicate_candidate = any(
            isinstance(item, dict)
            and (
                _normalize_sql_value(str(item.get("query_template") or "")),
                _normalize_sql_value(str(item.get("name") or "")),
                _normalize_sql_value(str(item.get("hypothesis") or "")),
            ) == normalized_identity
            for item in existing_candidates
        )
        if not duplicate_candidate:
            existing_candidates.append(validated)
            _record_artifact(state, "candidate_hypotheses", validated, append=True)

        result: Dict[str, Any] = {"ok": True, "candidate": validated}
        if isinstance(review_payload, dict):
            normalized_review = _normalize_review_decision(review_payload)
            ok, review_validated = _safe_model_validate(ReviewDecision, normalized_review)
            if not ok:
                return review_validated
            state.setdefault("review_decisions", []).append(review_validated)
            _record_artifact(state, "review_decisions", review_validated, append=True)
            result["review_decision"] = review_validated
            if review_validated.get("promotion_readiness"):
                promoted_payload = dict(validated)
                promoted_payload["promoted"] = True
                promoted_payload["status"] = "promoted"
                ok, promoted_validated = _safe_model_validate(PromotedInsight, promoted_payload)
                if not ok:
                    return promoted_validated
                promoted_list = state.setdefault("promoted_insights", [])
                duplicate_promoted = any(
                    isinstance(item, dict)
                    and _normalize_sql_value(str(item.get("query_template") or "")) == _normalize_sql_value(str(promoted_validated.get("query_template") or ""))
                    and _normalize_sql_value(str(item.get("name") or "")) == _normalize_sql_value(str(promoted_validated.get("name") or ""))
                    for item in promoted_list
                )
                if not duplicate_promoted:
                    promoted_list.append(promoted_validated)
                    _record_artifact(state, "promoted_insights", promoted_validated, append=True)
                result["promoted_insight"] = promoted_validated
        if isinstance(execution_payload, dict):
            ok, execution_validated = _safe_model_validate(ExecutionOutcome, execution_payload)
            if not ok:
                return execution_validated
            state.setdefault("execution_summaries", []).append(execution_validated)
            _record_artifact(state, "execution_summaries", execution_validated, append=True)
            result["execution_outcome"] = execution_validated
        _tool_cache_set(state, "store_candidate_insight", args_payload, result)
        return result
    except Exception as exc:
        return _tool_exception("store_candidate_insight", exc, state)


def _summary_markdown(report: Dict[str, Any]) -> str:
    objective = report.get("analysis_objective") or {}
    profile = report.get("database_profile") or {}
    promoted = report.get("promoted_insights") or []
    lines = [
        "# Telegram Chat Engagement Analysis",
        "",
        f"- Target chat UUID: {report.get('target_chat_uuid', '')}",
        f"- Run ID: {report.get('run_id', '')}",
        f"- Candidates proposed: {report.get('candidates_proposed', 0)}",
        f"- Promoted insights: {report.get('promoted', 0)}",
        f"- Query count total: {report.get('query_count_total', 0)}",
        "",
        "## Scope",
        "",
        f"- Success definition: {objective.get('success_definition', '')}",
        f"- Failure definition: {objective.get('failure_definition', '')}",
        f"- Success metric: {objective.get('success_metric', '')}",
        f"- Failure metric: {objective.get('failure_metric', '')}",
        f"- Success threshold: {objective.get('success_threshold_value', '')} {objective.get('threshold_unit', '')}",
        f"- Failure threshold: {objective.get('failure_threshold_value', '')} {objective.get('threshold_unit', '')}",
        f"- Admin exclusion: {objective.get('admin_exclusion_strategy', '')}",
        "",
        "## Inferred Schema",
        "",
        f"- Message table: {profile.get('inferred_message_table', '')}",
        f"- Reaction table: {profile.get('inferred_reaction_table', '')}",
        f"- Membership table: {profile.get('inferred_membership_table', '')}",
        f"- User table: {profile.get('inferred_user_table', '')}",
        "",
        "## Recommended Admin Actions",
        "",
    ]
    objective_evidence = objective.get("objective_evidence") or {}
    selected_thresholds = objective_evidence.get("selected_thresholds") or {}
    if selected_thresholds:
        lines.extend(
            [
                "## Threshold Rationale",
                "",
                f"- Selected thresholds: {json.dumps(selected_thresholds, ensure_ascii=True, sort_keys=True)}",
                "",
            ]
        )
    if not promoted:
        lines.append("- No promoted insights yet. Review candidate hypotheses and bootstrap schema inference.")
    for insight in promoted:
        evidence = insight.get("evidence_summary") or {}
        lines.extend(
            [
                f"### {insight.get('name', 'Insight')}",
                "",
                f"- Hypothesis: {insight.get('hypothesis', '')}",
                f"- Why it matters: {insight.get('interpretation', '')}",
                f"- Recommended action: {insight.get('admin_implication', '')}",
                f"- Evidence snapshot: {json.dumps(evidence, ensure_ascii=True, sort_keys=True)}",
                "",
            ]
        )
    lines.extend(
        [
            "## Caveats",
            "",
            "- Admin exclusion may rely on the top-message-sender heuristic if no explicit admin role data exists.",
            "- Findings are limited to one target chat and the discovered tables/columns available in the database.",
            "- Advanced causal/path models are not implemented in this version.",
            "",
        ]
    )
    return "\n".join(lines)


def finalize_analysis_report(tool_context: Any = None) -> Dict[str, Any]:
    state = _state_from_context(tool_context)
    try:
        args_payload = {"target_chat_uuid": str(state.get("target_chat_uuid") or "")}
        cached = _tool_cache_get(state, "finalize_analysis_report", args_payload)
        if cached is not None:
            return cached
        candidates = state.get("candidate_hypotheses") or []
        policy_validations = state.get("policy_validations") or []
        reviews = state.get("review_decisions") or []
        executions = state.get("execution_summaries") or []
        promoted = state.get("promoted_insights") or []
        last_loop_status = state.get("last_loop_status") or {}
        report_payload = ExperimentReport(
            run_id=_run_id_from_state(state),
            generated_at=_utc_now(),
            target_chat_uuid=str(state.get("target_chat_uuid") or ""),
            candidates_proposed=len(candidates),
            policy_blocked=sum(1 for item in policy_validations if isinstance(item, dict) and not item.get("allowed")),
            reviewer_rejected=sum(1 for item in reviews if isinstance(item, dict) and item.get("decision") == "reject"),
            executed_successfully=sum(1 for item in executions if isinstance(item, dict) and item.get("status") == "ok"),
            promoted=len(promoted),
            notes=[
                "Final report is optimized for chat admins.",
                "SQL execution is read-only and chat-scoped.",
            ],
        ).model_dump()
        report_payload["database_profile"] = state.get("database_profile") or {}
        report_payload["analysis_objective"] = state.get("analysis_objective") or {}
        report_payload["promoted_insights"] = promoted
        report_payload["query_count_total"] = int(state.get("query_count") or 0)
        report_payload["stop_reason"] = str(last_loop_status.get("reason") or "")
        report_payload["stop_iteration"] = int(last_loop_status.get("iteration_index") or 0)
        state["experiment_report"] = report_payload
        _record_artifact(state, "experiment_report", report_payload, append=False)

        summary_path = _run_dir(state) / "summary.md"
        summary_path.write_text(_summary_markdown(report_payload), encoding="utf-8")
        result = {
            "ok": True,
            "run_id": report_payload.get("run_id"),
            "target_chat_uuid": report_payload.get("target_chat_uuid"),
            "promoted_insights": promoted,
            "experiment_report_metrics": {
                "candidates_proposed": report_payload.get("candidates_proposed", 0),
                "policy_blocked": report_payload.get("policy_blocked", 0),
                "reviewer_rejected": report_payload.get("reviewer_rejected", 0),
                "executed_successfully": report_payload.get("executed_successfully", 0),
                "promoted": report_payload.get("promoted", 0),
                "query_count_total": report_payload.get("query_count_total", 0),
            },
            "experiment_report": report_payload,
            "summary_path": str(summary_path),
        }
        _tool_cache_set(state, "finalize_analysis_report", args_payload, result)
        return result
    except Exception as exc:
        return _tool_exception("finalize_analysis_report", exc, state)
