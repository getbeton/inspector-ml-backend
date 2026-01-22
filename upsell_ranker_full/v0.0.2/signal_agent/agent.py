import os

import agentops
from google.adk.agents import LlmAgent, LoopAgent
from google.adk.models.google_llm import Gemini
from google.adk.tools.exit_loop_tool import exit_loop
from google.adk.tools.function_tool import FunctionTool

from .tools import run_posthog_query

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model="gemini-2.5-flash")

signal_worker = LlmAgent(
    name="signal_worker",
    model=MODEL,
    tools=[run_posthog_query],
    instruction=(
        "You are the signal_agent worker. You receive DWH table summaries, join candidates, and company context.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior only. Never write, mutate, or delete data.\n"
        "- Use run_posthog_query for all SQL; queries must be SELECT/WITH only.\n"
        "- Keep queries small and safe; always include LIMIT and narrow filters.\n"
        "\n"
        "Workflow:\n"
        "1) Review join_candidates and pick 2-4 joins that link the most tables.\n"
        "2) Propose 2-4 simple signals (activation, expansion, churn risk) using DWH tables.\n"
        "3) Execute each query with run_posthog_query.\n"
        "4) Return JSON with concrete detections from query results.\n"
        "\n"
        "Output JSON only (schema is provisional):\n"
        "{\n"
        "  \"signal_summary\": {\n"
        "    \"status\": \"computed\",\n"
        "    \"signals\": [\n"
        "      {\n"
        "        \"signal_name\": \"...\",\n"
        "        \"signal_description\": \"...\",\n"
        "        \"sql\": \"SELECT ...\",\n"
        "        \"detections\": [\"...\"],\n"
        "        \"notes\": \"...\"\n"
        "      }\n"
        "    ],\n"
        "    \"notes\": \"...\"\n"
        "  }\n"
        "}\n"
    ),
)

signal_judge = LlmAgent(
    name="signal_judge",
    model=MODEL,
    tools=[FunctionTool(exit_loop)],
    instruction=(
        "You are a strict judge for signal_agent outputs.\n"
        "\n"
        "Approve only if:\n"
        "- At least one run_posthog_query result is present in context.\n"
        "- signal_summary.signals[] includes sql and detections from real rows.\n"
        "- All SQL is SELECT/WITH and appears read-only.\n"
        "\n"
        "If approved, call exit_loop and say nothing else.\n"
        "Otherwise respond with a short JSON critique and do NOT call exit_loop.\n"
    ),
)

root_agent = LoopAgent(
    name="signal_agent",
    sub_agents=[signal_worker, signal_judge],
    max_iterations=int(os.getenv("SIGNAL_AGENT_MAX_LOOPS", "3")),
)
