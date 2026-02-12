import os
from typing import Any, Dict, List

import agentops
from shared.inspector import inspector_env, inspector_post
from google.adk.agents.llm_agent import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model=os.getenv("DWH_ANALYST_MODEL", "gemini-3-flash-preview"))

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


def _build_dwh_fallback(state_payload: Dict[str, Any]) -> Dict[str, Any]:
    table_summaries = state_payload.get("table_summaries") or []
    join_candidates = state_payload.get("join_candidates") or []
    tables = [t.get("name") for t in table_summaries if isinstance(t, dict) and t.get("name")]
    status = state_payload.get("status") or "unknown"
    summary_text = ""
    if tables or join_candidates:
        summary_text = (
            f"Found {len(tables)} tables and {len(join_candidates)} join candidates "
            "from the warehouse sample."
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
        for table in table_summaries:
            if not isinstance(table, dict):
                continue
            table_id = table.get("table_id") or table.get("name") or ""
            table_name = table.get("name") or table_id
            columns = table.get("columns") or []
            total_rows = table.get("total_rows")
            total_bytes = table.get("total_bytes")
            disk_size_gb = None
            if isinstance(total_bytes, (int, float)):
                disk_size_gb = round(total_bytes / 1_000_000_000, 6)
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
            eda_entry = {
                "workspace_id": workspace_id,
                "table_id": table_id or table_name,
                "join_suggestions": join_suggestions or None,
                "metrics_discovery": None,
                "table_stats": {
                    "row_count": total_rows,
                    "disk_size_gb": disk_size_gb,
                    "columns_count": table.get("column_count") or len(columns),
                },
                "summary_text": f"{table_name} table with {len(columns)} columns.",
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


root_agent = Agent(
    model=MODEL,
    name="dwh_analyst",
    description="Explores the user's DWH to find relevant tables, columns, and join candidates.",
    generate_content_config=types.GenerateContentConfig(
        response_mime_type="application/json"
    ),
    tools=[inspector_dwh_eda, emit_dwh_analytics],
    instruction=(
        "You are the dwh_analyst. You receive company context and must explore the user's DWH.\n"
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
        "2) Call inspector_dwh_eda to discover tables/columns and pull small samples.\n"
        "3) Identify candidate tables for accounts/clients, usage, and revenue/billing.\n"
        "4) Propose explicit join candidates as table/column pairs; note uncertainty when keys are ambiguous.\n"
        "5) Summarize and return results to upsell_agent.\n"
        "6) Include workspace_id and session_id from the input in your final JSON payload.\n"
        "7) Call emit_dwh_analytics exactly once with the final JSON payload.\n"
        "   Use the inspector_dwh_eda response to fill table_summaries and join_candidates.\n"
        "\n"
        "Output JSON (schema is provisional):\n"
        "{\n"
        "  \"schema_version\": \"v0.0.1\",\n"
        "  \"summary_text\": \"2-4 sentences on discovered tables + join hints.\",\n"
        "  \"dwh_summary\": {\"status\": \"...\", \"notes\": \"...\", \"tables\": [], \"joins\": [], \"metrics\": []},\n"
        "  \"table_summaries\": [],\n"
        "  \"join_candidates\": [],\n"
        "  \"workspace_id\": \"...\",\n"
        "  \"session_id\": \"...\"\n"
        "}\n"
    ),
)
