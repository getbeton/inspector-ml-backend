import os
from typing import Dict

import agentops
from google.adk.agents.llm_agent import Agent
from google.adk.models.google_llm import Gemini

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model="gemini-2.5-flash")

from signal_agent.agent import root_agent as signal_agent
from .tools import posthog_dwh_eda

root_agent = Agent(
    model=MODEL,
    name="dwh_analyst",
    description="Explores the user's DWH to find relevant tables, joins, and usage signals.",
    tools=[posthog_dwh_eda],
    sub_agents=[signal_agent],
    instruction=(
        "You are the dwh_analyst. You receive company context and must explore the user's DWH.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior only. Never write, mutate, or delete data.\n"
        "- Do not assume schema or table names. Discover them from metadata or inspection.\n"
        "- Ignore PostHog events for now; focus only on Data Warehouse sources.\n"
        "\n"
        "Workflow:\n"
        "1) Call posthog_dwh_eda to discover tables/columns and pull small samples.\n"
        "2) Identify candidate tables for accounts/clients, usage, and revenue/billing.\n"
        "3) Propose explicit join candidates as table/column pairs; note uncertainty when keys are ambiguous.\n"
        "4) Call the signal_agent sub-agent with a payload including table_summaries, join_candidates, and assumptions.\n"
        "5) After signal_agent returns, summarize and return results to upsell_agent.\n"
        "\n"
        "Output JSON (schema is provisional):\n"
        "{\n"
        "  \"dwh_summary\": {\"status\": \"...\", \"notes\": \"...\", \"tables\": [], \"joins\": [], \"metrics\": []},\n"
        "  \"signal_summary\": {\"status\": \"...\", \"signals\": [], \"notes\": \"...\"}\n"
        "}\n"
    ),
)
