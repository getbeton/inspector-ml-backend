import os
from typing import Any, Dict, List

import agentops
from google.adk.agents.llm_agent import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model="gemini-3-flash-preview")

from .tools import posthog_dwh_eda

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
        if state is not None and isinstance(state.get("posthog_dwh_eda"), dict):
            eda = state["posthog_dwh_eda"]
            if not payload.get("table_summaries") and not payload.get("join_candidates"):
                payload = {**payload, **_build_dwh_fallback(eda)}

    validated = DwhAnalytics.model_validate(payload)
    data = validated.model_dump()
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None:
            state["dwh_analytics"] = data
        actions = getattr(tool_context, "actions", None)
        if actions is not None:
            actions.skip_summarization = True
    return data


root_agent = Agent(
    model=MODEL,
    name="dwh_analyst",
    description="Explores the user's DWH to find relevant tables, columns, and join candidates.",
    generate_content_config=types.GenerateContentConfig(
        response_mime_type="application/json"
    ),
    tools=[posthog_dwh_eda, emit_dwh_analytics],
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
        "1) If input includes posthog_token, posthog_host, or posthog_project_id, pass them to posthog_dwh_eda.\n"
        "2) Call posthog_dwh_eda to discover tables/columns and pull small samples.\n"
        "3) Identify candidate tables for accounts/clients, usage, and revenue/billing.\n"
        "4) Propose explicit join candidates as table/column pairs; note uncertainty when keys are ambiguous.\n"
        "5) Summarize and return results to upsell_agent.\n"
        "6) Call emit_dwh_analytics exactly once with the final JSON payload.\n"
        "   Use the posthog_dwh_eda response to fill table_summaries and join_candidates.\n"
        "\n"
        "Output JSON (schema is provisional):\n"
        "{\n"
        "  \"schema_version\": \"v0.0.1\",\n"
        "  \"summary_text\": \"2-4 sentences on discovered tables + join hints.\",\n"
        "  \"dwh_summary\": {\"status\": \"...\", \"notes\": \"...\", \"tables\": [], \"joins\": [], \"metrics\": []},\n"
        "  \"table_summaries\": [],\n"
        "  \"join_candidates\": []\n"
        "}\n"
    ),
)
