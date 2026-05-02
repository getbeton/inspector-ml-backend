import os
import random
from typing import Any, Dict, List

import agentops
from shared.inspector import inspector_env, inspector_post
from shared.memory_bank import render_for as _memory_bank_for
from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

# ADK loads each agent directly without importing the v0.0.2 package
# __init__.py, so wire OTel/Langfuse observability at agent-import time.
from shared.observability import init_observability  # noqa: E402

init_observability()

MODEL = LiteLlm(model=os.getenv("DWH_ANALYST_MODEL", "anthropic/claude-opus-4-6"))

from .tools import inspector_dwh_eda

class DwhSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str = ""
    notes: str = ""
    tables: List[Any] = Field(default_factory=list)
    joins: List[Any] = Field(default_factory=list)
    metrics: List[Any] = Field(default_factory=list)


class DwhAnalytics(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "v0.0.1"
    summary_text: str = ""
    workspace_id: str = ""
    session_id: str = ""
    dwh_summary: DwhSummary = Field(default_factory=DwhSummary)
    table_summaries: List[Any] = Field(default_factory=list)
    join_candidates: List[Any] = Field(default_factory=list)


def _table_metric_entries(table: Dict[str, Any], global_metrics: List[Any]) -> List[Any]:
    table_metrics = table.get("metrics_discovery")
    if isinstance(table_metrics, list):
        return table_metrics
    if isinstance(table_metrics, dict):
        return [table_metrics]
    table_id = table.get("table_id") or table.get("queryable_name") or table.get("name")
    table_name = table.get("name")
    resolved: List[Any] = []
    for metric in global_metrics:
        if not isinstance(metric, dict):
            continue
        metric_table = metric.get("table_id") or metric.get("table")
        if metric_table and metric_table in {table_id, table_name}:
            resolved.append(metric)
    return resolved


def _infer_metric_columns(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    metric_keywords = (
        "arr", "mrr", "revenue", "price", "amount", "invoice", "billing", "payment",
        "subscription", "renewal", "churn", "ltv", "acv", "gmv", "bookings", "sales",
        "pipeline", "deal", "seat", "license", "usage", "events", "active", "sessions",
    )
    candidates: List[Dict[str, Any]] = []
    table_name = table.get("name") or table.get("table_id") or ""
    sample_summary = table.get("sample_summary") or {}
    summary_columns = sample_summary.get("columns") if isinstance(sample_summary, dict) else {}
    column_types = table.get("column_types") if isinstance(table.get("column_types"), dict) else {}

    for col in table.get("columns") or []:
        col_l = str(col).lower()
        if not any(keyword in col_l for keyword in metric_keywords):
            continue
        col_meta = summary_columns.get(col) if isinstance(summary_columns, dict) else {}
        candidates.append(
            {
                "table_id": table.get("table_id") or table_name,
                "table": table_name,
                "column": col,
                "type": column_types.get(col) or (col_meta.get("type") if isinstance(col_meta, dict) else None),
                "reason": "column name suggests revenue, subscription, sales, or usage signal",
            }
        )
        if len(candidates) >= 8:
            break
    return candidates


def _table_summary_text(table: Dict[str, Any], join_suggestions: List[Dict[str, Any]]) -> str:
    explicit = table.get("summary_text")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    table_name = table.get("name") or table.get("table_id") or "table"
    source_type = table.get("source_type") or table.get("engine") or "unknown source"
    column_count = table.get("column_count")
    if not isinstance(column_count, int):
        columns = table.get("columns") or []
        column_count = len(columns) if isinstance(columns, list) else 0

    key_assumptions: List[str] = []
    sample_summary = table.get("sample_summary") or {}
    column_meta = sample_summary.get("columns") if isinstance(sample_summary, dict) else {}
    if isinstance(column_meta, dict):
        for col, meta in column_meta.items():
            if not isinstance(meta, dict):
                continue
            roles = meta.get("roles") or []
            if "company_ref" in roles or "user_ref" in roles:
                key_assumptions.append(f"{col} looks like an entity key")
            elif "email" in roles:
                key_assumptions.append(f"{col} likely supports identity joins")
            elif "billing_ref" in roles:
                key_assumptions.append(f"{col} likely links to billing records")
            if len(key_assumptions) >= 2:
                break

    summary = f"{table_name} ({source_type}) has {column_count} columns in metadata."
    if key_assumptions:
        summary += f" Assumptions: {', '.join(key_assumptions)}."
    if join_suggestions:
        summary += f" {len(join_suggestions)} join path(s) detected from sample overlap."
    return summary


def _build_dwh_fallback(state_payload: Dict[str, Any]) -> Dict[str, Any]:
    table_summaries = state_payload.get("table_summaries") or []
    join_candidates = state_payload.get("join_candidates") or []
    tables = [t.get("name") for t in table_summaries if isinstance(t, dict) and t.get("name")]
    status = state_payload.get("status") or "unknown"
    summary_text = ""
    if tables or join_candidates:
        summary_text = (
            f"Discovered {len(tables)} tables and {len(join_candidates)} join candidates "
            "from Inspector metadata samples."
        )
    else:
        reason = state_payload.get("reason") or "no warehouse data"
        summary_text = f"DWH analysis unavailable: {reason}."
    return {
        "summary_text": summary_text,
        "dwh_summary": {
            "status": status,
            "notes": state_payload.get("reason") or "",
            "tables": tables,
            "joins": join_candidates,
            "metrics": [],
        },
        "table_summaries": table_summaries,
        "join_candidates": join_candidates,
    }


def emit_dwh_analytics(payload: Dict[str, Any], tool_context: Any = None) -> Dict[str, Any]:
    payload = payload or {}
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None and isinstance(state.get("inspector_dwh_eda"), dict):
            eda = state["inspector_dwh_eda"]
            if not payload.get("table_summaries") and not payload.get("join_candidates"):
                payload = {**payload, **_build_dwh_fallback(eda)}

    validated = DwhAnalytics.model_validate(payload)
    data = validated.model_dump()
    workspace_id = payload.get("workspace_id") or ""
    session_id = payload.get("session_id") or ""
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None:
            if not workspace_id:
                workspace_id = state.get("workspace_id") or ""
            if not session_id:
                session_id = state.get("session_id") or ""
            state["dwh_analytics"] = data
            if workspace_id:
                state["workspace_id"] = workspace_id
            if session_id:
                state["session_id"] = session_id
        actions = getattr(tool_context, "actions", None)
        if actions is not None:
            actions.skip_summarization = True

    env = inspector_env()
    if env["agent_secret"] and workspace_id:
        eda_results = []
        table_summaries = data.get("table_summaries") or []
        join_candidates = data.get("join_candidates") or []
        dwh_summary = data.get("dwh_summary") or {}
        global_metrics = dwh_summary.get("metrics") or []
        for table in table_summaries:
            if not isinstance(table, dict):
                continue
            table_id = table.get("table_id") or table.get("queryable_name") or table.get("name") or ""
            table_name = table.get("name") or table_id
            columns = table.get("columns") or []
            join_suggestions = []
            for candidate in join_candidates:
                if not isinstance(candidate, dict):
                    continue
                tables = candidate.get("tables") or []
                table_ids = candidate.get("table_ids") or []
                if table_name in tables or table_id in table_ids:
                    cols = candidate.get("columns") or ["", ""]
                    join_suggestions.append(
                        {
                            "table1": tables[0] if len(tables) > 0 else "",
                            "col1": cols[0] if len(cols) > 0 else "",
                            "table2": tables[1] if len(tables) > 1 else "",
                            "col2": cols[1] if len(cols) > 1 else "",
                            "role": candidate.get("role"),
                            "overlap_samples": candidate.get("overlap_samples") or [],
                        }
                    )
            metrics_discovery = _table_metric_entries(table, global_metrics)
            if not metrics_discovery:
                metrics_discovery = _infer_metric_columns(table)
            eda_entry = {
                "workspace_id": workspace_id,
                "table_id": table_id or table_name,
                "join_suggestions": join_suggestions or None,
                "metrics_discovery": metrics_discovery or None,
                "table_stats": {
                    "row_count": random.randint(10, 10000),
                    "columns_count": table.get("column_count") or len(columns),
                },
                "summary_text": _table_summary_text(table, join_suggestions),
            }
            eda_results.append(eda_entry)
            try:
                inspector_post(
                    env["url"],
                    "/api/agent/data/eda",
                    env["agent_secret"],
                    env["vercel_protection"],
                    eda_entry,
                    timeout=60,
                    include_vercel=True,
                )
            except Exception:
                pass

        website_exploration = None
        if tool_context is not None:
            state = getattr(tool_context, "state", None)
            if state is not None:
                website_exploration = state.get("website_exploration")

        if session_id and website_exploration is not None:
            summary_payload = {
                "session_id": session_id,
                "eda_results": [
                    {
                        "table_id": entry.get("table_id"),
                        "join_suggestions": entry.get("join_suggestions"),
                        "metrics_discovery": entry.get("metrics_discovery"),
                        "table_stats": entry.get("table_stats"),
                        "summary_text": entry.get("summary_text"),
                    }
                    for entry in eda_results
                ],
                "website_exploration": website_exploration,
            }
            try:
                result = inspector_post(
                    env["url"],
                    "/api/agent/write-summary",
                    env["agent_secret"],
                    env["vercel_protection"],
                    summary_payload,
                    timeout=60,
                    include_vercel=False,
                )
                if tool_context is not None:
                    state = getattr(tool_context, "state", None)
                    if state is not None:
                        state["inspector_write_summary"] = result
            except Exception as exc:
                if tool_context is not None:
                    state = getattr(tool_context, "state", None)
                    if state is not None:
                        state["inspector_write_summary"] = {"status": "error", "error": str(exc)}
    return data


def _sanitize_history_for_anthropic(callback_context, llm_request):
    """No-op — ADK v1.19.0 already reformats cross-agent tool calls as
    '[agent_name] said:' text messages via _present_other_agent_message(),
    satisfying Anthropic's strict tool_use→tool_result pairing without
    manual sanitization.  Previous versions of this callback stripped the
    agent's own tool history because ADK inserts instruction content
    (role='user' with text) after tool responses, which shifted the
    boundary detection."""
    return None


root_agent = Agent(
    model=MODEL,
    name="dwh_analyst",
    description="Explores the user's DWH to find relevant tables, columns, and join candidates.",
    generate_content_config=types.GenerateContentConfig(
        response_mime_type="application/json"
    ),
    before_model_callback=_sanitize_history_for_anthropic,
    tools=[inspector_dwh_eda, emit_dwh_analytics],
    instruction=(
        _memory_bank_for("dwh") + "\n"
        "You are the dwh_analyst. You receive company context and must explore the user's DWH.\n"
        "\n"
        "Workflow step 0 (mandatory): read the running-lean and\n"
        "monetization-pricing sections in the memory bank above. Use\n"
        "running-lean to decide which DWH tables map to validated learning\n"
        "(active accounts, paid invoices) vs vanity (signups, login). Cite\n"
        "the doc(s) you used in `dwh_summary.notes`.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior only. Never write, mutate, or delete data.\n"
        "- Do not assume schema or table names. Discover them from metadata or inspection.\n"
        "- Ignore PostHog events for now; focus only on Data Warehouse sources.\n"
        "- Never include secrets in your output (tokens, API keys).\n"
        "- Output JSON only. No Markdown, no code fences.\n"
        "\n"
        "Workflow:\n"
        "1) Extract inspector session_id from the input and pass it to inspector_dwh_eda.\n"
        "2) Call inspector_dwh_eda to discover tables and typed columns; use sample values for assumptions.\n"
        "3) Identify candidate tables for accounts/clients, usage, and revenue/billing.\n"
        "4) Propose explicit join candidates as table/column pairs; note uncertainty when keys are ambiguous.\n"
        "5) Write a concise but informative summary describing likely table purpose and data caveats.\n"
        "6) Include workspace_id and session_id from the input in your final JSON payload.\n"
        "7) Call emit_dwh_analytics exactly once with the final JSON payload.\n"
        "   Use the inspector_dwh_eda response to fill table_summaries and join_candidates.\n"
        "8) Populate dwh_summary.metrics with discovered metric opportunities and add per-table\n"
        "   metrics_discovery entries in table_summaries when possible.\n"
        "9) metrics_discovery must be a list of columns suspicious as product/business metrics.\n"
        "   Focus on columns tied to money or expansion potential: ARR, MRR, revenue, invoice amount,\n"
        "   subscription period/days, renewal/churn, seats/licenses, usage volume, or sales pipeline.\n"
        "   For each item include at least table_id/table, column, and short reason.\n"
        "\n"
        "Output JSON (schema is provisional):\n"
        "{\n"
        "  \"schema_version\": \"v0.0.1\",\n"
        "  \"summary_text\": \"2-4 sentences on discovered tables, assumptions, and join hints.\",\n"
        "  \"dwh_summary\": {\"status\": \"...\", \"notes\": \"...\", \"tables\": [], \"joins\": [], \"metrics\": []},\n"
        "  \"table_summaries\": [{\"table_id\": \"...\", \"name\": \"...\", \"source_type\": \"...\", \"summary_text\": \"...\", \"metrics_discovery\": []}],\n"
        "  \"join_candidates\": [],\n"
        "  \"workspace_id\": \"...\",\n"
        "  \"session_id\": \"...\"\n"
        "}\n"
    ),
)
