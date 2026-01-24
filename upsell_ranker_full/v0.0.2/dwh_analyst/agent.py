import os
from typing import Dict

import agentops
from google.adk.agents.llm_agent import Agent
from google.adk.models.google_llm import Gemini

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model="gemini-2.5-flash")

from .tools import posthog_dwh_eda

root_agent = Agent(
    model=MODEL,
    name="dwh_analyst",
    description="Explores the user's DWH to find relevant tables, columns, and join candidates.",
    tools=[posthog_dwh_eda],
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
