import os
from typing import Any, Dict, List

import agentops
import requests
from google.adk.agents import SequentialAgent
from google.adk.agents.llm_agent import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model="gemini-3-flash-preview")

from shared.cache import cache_read_json, cache_write_json

class CompanySummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    website: str = ""
    business_model: str = ""
    product: str = ""
    icp: str = ""
    pricing_model: Any = ""
    assumptions: List[str] = Field(default_factory=list)


class DwhRequestSuggestions(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: str = ""
    items: List[Any] = Field(default_factory=list)


class WebsiteContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    website_url: str = ""
    company_summary: CompanySummary = Field(default_factory=CompanySummary)
    assumptions: List[str] = Field(default_factory=list)
    upsell_summary_text: str = ""
    dwh_request_suggestions: DwhRequestSuggestions = Field(default_factory=DwhRequestSuggestions)


def emit_website_summary(
    website_url: str,
    business_model: str,
    product: str,
    icp: str,
    pricing_model: Any,
    upsell_summary_text: str,
    dwh_request_suggestions: Dict[str, Any],
    assumptions: List[str] | None = None,
    tool_context: Any = None,
) -> Dict[str, Any]:
    """
    Emit a structured website summary.

    Fill the fields from the homepage content, product positioning, and pricing hints.
    Avoid leaving business_model, product, or icp as "unknown" unless the site truly lacks detail.

    Example payload:
    {
      "website_url": "https://acme-analytics.com",
      "business_model": "B2B SaaS, PLG (self-serve + sales-assisted enterprise)",
      "product": "Product analytics with event tracking, session replay, funnels, and dashboards.",
      "icp": "Mid-market B2B SaaS companies (50-500 employees) with product/data teams.",
      "pricing_model": {
        "model": "usage_based",
        "free_tier": true,
        "tiers": [
          {"name": "Free", "limit": "1M events/mo", "price": 0},
          {"name": "Growth", "limit": "10M events/mo", "price": 450},
          {"name": "Enterprise", "limit": "Unlimited", "price": "Custom"}
        ]
      },
      "upsell_summary_text": "Acme Analytics is a product analytics platform for B2B SaaS teams...",
      "assumptions": [
        "Self-serve signup with free tier",
        "Collaboration features drive expansion"
      ],
      "dwh_request_suggestions": {
        "summary": "Provide usage and billing tables to identify expansion signals.",
        "items": []
      }
    }
    """
    payload: Dict[str, Any] = {
        "website_url": website_url,
        "company_summary": {
            "website": website_url,
            "business_model": business_model,
            "product": product,
            "icp": icp,
            "pricing_model": pricing_model,
            "assumptions": assumptions or [],
        },
        "upsell_summary_text": upsell_summary_text,
        "dwh_request_suggestions": dwh_request_suggestions,
    }
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None and isinstance(state.get("homepage_fetch"), dict):
            homepage = state["homepage_fetch"]
            if not payload.get("website_url"):
                payload["website_url"] = homepage.get("url", "")
            if isinstance(payload.get("company_summary"), dict):
                if not payload["company_summary"].get("website"):
                    payload["company_summary"]["website"] = homepage.get("url", "")

    summary = payload.get("company_summary") or {}
    for key in ("business_model", "product", "icp"):
        if not summary.get(key):
            summary[key] = "unknown"
    payload["company_summary"] = summary

    if not payload.get("dwh_request_suggestions"):
        payload["dwh_request_suggestions"] = {"summary": "Provide DWH usage and billing tables.", "items": []}
    elif not payload["dwh_request_suggestions"].get("summary"):
        payload["dwh_request_suggestions"]["summary"] = "Provide DWH usage and billing tables."

    if not summary.get("assumptions"):
        if summary.get("business_model") == "unknown" or summary.get("product") == "unknown" or summary.get("icp") == "unknown":
            summary["assumptions"] = ["Insufficient website detail; fields set to unknown."]

    validated = WebsiteContext.model_validate(payload)
    data = validated.model_dump()
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None:
            state["website_context"] = data
            state["website_summary"] = data
        actions = getattr(tool_context, "actions", None)
        if actions is not None:
            actions.skip_summarization = True
    return data


def fetch_company_homepage(
    url: str,
    timeout_sec: int = 12,
    tool_context: Any = None,
) -> Dict[str, Any]:
    cache_key = f"homepage_fetch:v1:url={url}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        if tool_context is not None:
            state = getattr(tool_context, "state", None)
            if state is not None:
                state["homepage_fetch"] = cached
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
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None:
            state["homepage_fetch"] = payload
    return payload

from dwh_analyst.agent import root_agent as dwh_analyst

upsell_worker = Agent(
    model=MODEL,
    name="upsell_worker",
    description="Explores user company context and captures website summary context.",
    generate_content_config=types.GenerateContentConfig(
        response_mime_type="application/json"
    ),
    tools=[
        fetch_company_homepage,
        emit_website_summary,
    ],
    instruction=(
        "You are the upsell_worker for the upsale opportunity system. The user provides a company website URL.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior: never write, mutate, or delete data. Only read and summarize.\n"
        "- Use tools conservatively and explain missing data as assumptions.\n"
        "- Prefer the company's official website and avoid domain parking/sale pages or unrelated directories.\n"
        "- Never include secrets in your output (tokens, API keys).\n"
        "- Output JSON only. No Markdown, no code fences.\n"
        "\n"
        "Workflow:\n"
        "1) If the input does not include a website URL, ask for it.\n"
        "2) Call fetch_company_homepage to retrieve the front page HTML only.\n"
        "   Summarize what the company sells and how (B2B vs PLG, main product, ICP, pricing model).\n"
        "   If the page cannot be fetched, proceed using the URL host and explicit assumptions.\n"
        "3) Build a website context payload with: website_url, business_model, product, icp,\n"
        "   pricing_model, assumptions, upsell_summary_text, and dwh_request_suggestions.\n"
        "4) Call emit_website_summary exactly once with that payload.\n"
        "   Do not leave fields blank; fill business_model, product, icp, and pricing_model from the homepage.\n"
        "   If pricing is unclear, add 2-4 assumptions about pricing/PLG motion instead of leaving fields empty.\n"
        "\n"
        "Output JSON for emit_website_summary (schema is provisional):\n"
        "{\n"
        "  \"website_url\": \"...\",\n"
        "  \"upsell_summary_text\": \"2-4 sentences summarizing the website + suggested DWH focus.\",\n"
        "  \"business_model\": \"...\",\n"
        "  \"product\": \"...\",\n"
        "  \"icp\": \"...\",\n"
        "  \"pricing_model\": \"...\",\n"
        "  \"assumptions\": [],\n"
        "  \"dwh_request_suggestions\": {\"summary\": \"1-3 sentences covering the requested DWH follow-ups.\", \"items\": []}\n"
        "}\n"
        "\n"
        "If any tool is stubbed or unavailable, keep going and describe limitations in notes.\n"
    ),
)

root_agent = SequentialAgent(
    name="upsell_pipeline",
    sub_agents=[upsell_worker, dwh_analyst],
)
