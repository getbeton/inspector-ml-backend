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

root_agent = Agent(
    model=MODEL,
    name="dwh_analyst",
    description="Explores the user's DWH to find relevant tables, joins, and usage signals.",
    sub_agents=[signal_agent],
    instruction=(
        "You are the dwh_analyst. You receive company context and must explore the user's DWH.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior only. Never write, mutate, or delete data.\n"
        "- Do not assume schema or table names. Discover them from metadata or inspection.\n"
        "\n"
        "Workflow:\n"
        "1) Identify candidate tables for accounts/clients, events/usage, and revenue/billing.\n"
        "2) Propose join keys and filters; note uncertainty when keys are ambiguous.\n"
        "3) Call the signal_agent sub-agent with a payload including any table/field candidates and assumptions.\n"
        "4) After signal_agent returns, summarize and return results to upsell_agent.\n"
        "\n"
        "Output JSON (schema is provisional):\n"
        "{\n"
        "  \"dwh_summary\": {\"status\": \"...\", \"notes\": \"...\", \"tables\": [], \"joins\": [], \"metrics\": []},\n"
        "  \"signal_summary\": {\"status\": \"...\", \"signals\": [], \"notes\": \"...\"}\n"
        "}\n"
    ),
)
