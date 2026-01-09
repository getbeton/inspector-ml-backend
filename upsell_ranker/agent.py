import os
from google.adk.agents.llm_agent import Agent
from .tools_attio import refresh_attio_dump
from .scoring import rank_accounts

ADK_MODEL = 'gemini-2.5-flash'

root_agent = Agent(
    name="upsell_ranker",
    model=ADK_MODEL,
    description="Pulls Attio CRM data and ranks accounts for upsell with reasons + next actions.",
    instruction=(
        "You are a RevOps assistant. Your goal is to produce a simple but credible upsell ranking.\n"
        "When asked to rank accounts:\n"
        "1) If the user requests latest data, call refresh_attio_dump().\n"
        "2) Call rank_accounts(top_n=...).\n"
        "3) Present results as a numbered list: Company — Score — 2-3 reasons — Suggested next action.\n"
        "Be concise and business-like. Do not invent CRM facts that are not present in tool output."
        "Never call refresh_attio_dump unless the user explicitly asks to refresh/latest. "
        "Prefer cached data. When ranking, call rank_accounts(top_n=...)."
    ),
    tools=[refresh_attio_dump, rank_accounts],
)