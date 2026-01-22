import os
from typing import Any, Dict

import agentops
import requests
from google.adk.agents.llm_agent import Agent
from google.adk.models.google_llm import Gemini

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model="gemini-2.5-flash")

from shared.cache import cache_read_json, cache_write_json

def fetch_company_homepage(url: str, timeout_sec: int = 12) -> Dict[str, Any]:
    cache_key = f"homepage_fetch:v1:url={url}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return cached

    try:
        resp = requests.get(url, timeout=timeout_sec)
        payload = {
            "status": "success",
            "url": url,
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type"),
            "text": resp.text[:120_000],
        }
    except Exception as exc:
        payload = {
            "status": "error",
            "url": url,
            "error": str(exc),
        }
    cache_write_json(cache_key, payload)
    return payload

from dwh_analyst.agent import root_agent as dwh_analyst

root_agent = Agent(
    model=MODEL,
    name="upsell_agent",
    description="Explores user company context, orchestrates DWH analysis, and returns upsell rankings.",
    tools=[
        fetch_company_homepage,
    ],
    sub_agents=[dwh_analyst],
    instruction=(
        "You are the upsell_agent for the upsale opportunity system. The user provides a company website URL.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior: never write, mutate, or delete data. Only read and summarize.\n"
        "- Use tools conservatively and explain missing data as assumptions.\n"
        "- Prefer the company's official website and avoid domain parking/sale pages or unrelated directories.\n"
        "- Ratings must be floats in the range 0-100.\n"
        "- Use client names from the DWH for upsell_rankings[].name.\n"
        "- Include the product/plan in upsell_rankings[].proposal.\n"
        "\n"
        "Workflow:\n"
        "1) If the input does not include a website URL, ask for it.\n"
        "2) Call fetch_company_homepage to retrieve the front page HTML only.\n"
        "   Summarize what the company sells and how (B2B vs PLG, main product, ICP, pricing model).\n"
        "   If the page cannot be fetched, proceed using the URL host and explicit assumptions.\n"
        "3) Call the dwh_analyst sub-agent with a payload that includes: website_url, company_summary, assumptions.\n"
        "   The analyst should explore the DWH without assuming schema or table names and should call signal_agent.\n"
        "4) After dwh_analyst returns with signal_summary, produce the final response as JSON only. Do not wrap in Markdown.\n"
        "\n"
        "Output JSON (schema is provisional):\n"
        "{\n"
        "  \"schema_version\": \"v0.0.1\",\n"
        "  \"company_summary\": {\"website\": \"...\", \"business_model\": \"...\", \"product\": \"...\", \"icp\": \"...\", \"assumptions\": []},\n"
        "  \"dwh_summary\": {\"status\": \"...\", \"notes\": \"...\", \"tables\": [], \"metrics\": []},\n"
        "  \"signal_summary\": {\"status\": \"...\", \"signals\": [], \"notes\": \"...\"},\n"
        "  \"upsell_rankings\": [\n"
        "     {\"name\": \"...\", \"rating\": 0.0, \"proposal\": \"<product_or_plan>: <rationale>\", \"reasons\": [], \"next_steps\": []}\n"
        "  ],\n"
        "  \"notes\": []\n"
        "}\n"
        "\n"
        "If any tool is stubbed or unavailable, keep going and describe limitations in notes.\n"
    ),
)
