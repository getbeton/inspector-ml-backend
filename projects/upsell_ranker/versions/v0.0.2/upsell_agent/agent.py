"""Entry agent for v0.0.2 that turns website context into a working upsell hypothesis."""

import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests
from google.adk.agents import SequentialAgent
from google.adk.agents.llm_agent import Agent
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from shared.cache import cache_read_json, cache_write_json
from shared.inspector import inspector_env, inspector_post
from shared.model_factory import build_model
from shared.observability import init_observability
from shared.skills import build_skill_toolset

# ADK loads each agent module directly without importing the v0.0.2 package
# __init__.py, so wire AgentOps + Langfuse observability at agent-import time.
init_observability()

MODEL = build_model("UPSELL_AGENT_MODEL", "gemini-3-flash-preview")

_SKILL_TOOLSET = build_skill_toolset()
_SKILL_TOOLS: List[Any] = [_SKILL_TOOLSET] if _SKILL_TOOLSET is not None else []


class _HomepageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.headings: List[str] = []
        self._in_title = False
        self._current_heading = ""
        self._text_chunks: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in {"h1", "h2", "h3"}:
            self._current_heading = tag
        if tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"} and not self.meta_description:
                self.meta_description = (attrs_dict.get("content") or "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"h1", "h2", "h3"}:
            self._current_heading = ""

    def handle_data(self, data: str) -> None:
        text = unescape(data or "").strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        if self._current_heading and len(self.headings) < 12:
            self.headings.append(text)
        self._text_chunks.append(text)

    def extracted_text(self, char_limit: int) -> str:
        normalized = re.sub(r"\s+", " ", " ".join(self._text_chunks)).strip()
        return normalized[: max(200, char_limit)]


def _summarize_homepage_html(html: str) -> Dict[str, Any]:
    parser = _HomepageExtractor()
    parser.feed(html or "")
    char_limit = int(os.getenv("UPSELL_AGENT_HOMEPAGE_CHAR_LIMIT", "12000"))
    return {
        "title": parser.title,
        "meta_description": parser.meta_description,
        "headings": parser.headings[:8],
        "text_excerpt": parser.extracted_text(char_limit),
    }


def _infer_is_b2b(business_model: str) -> bool:
    return "b2b" in business_model.lower()


def _infer_plg_type(business_model: str) -> str:
    lower = business_model.lower()
    has_plg = "plg" in lower or "self-serve" in lower or "self serve" in lower
    has_sales = "sales-led" in lower or "sales led" in lower
    if has_plg and has_sales:
        return "hybrid"
    if has_plg:
        return "plg"
    if has_sales:
        return "slg"
    return "not_applicable"


def _normalize_website_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    value = value.strip(" \t\r\n,;<>\"'")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        return value
    bare_domain = re.match(
        r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/[^\s]*)?$",
        value,
    )
    if bare_domain:
        return f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return value
    return value

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
    workspace_id: str = ""
    company_summary: CompanySummary = Field(default_factory=CompanySummary)
    assumptions: List[str] = Field(default_factory=list)
    upsell_summary_text: str = ""
    dwh_request_suggestions: DwhRequestSuggestions = Field(default_factory=DwhRequestSuggestions)


class PipelineResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    website_context: Dict[str, Any] = Field(default_factory=dict)
    dwh_analytics: Dict[str, Any] = Field(default_factory=dict)
    signal_report: Dict[str, Any] = Field(default_factory=dict)
    promoted_signals: List[Any] = Field(default_factory=list)
    signal_objective: Dict[str, Any] = Field(default_factory=dict)
    warehouse_profile: Dict[str, Any] = Field(default_factory=dict)
    signal_run_id: str = ""
    summary_path: str = ""
    pipeline_status: Dict[str, Any] = Field(default_factory=dict)


def emit_website_summary(
    website_url: str,
    business_model: str,
    product: str,
    icp: str,
    pricing_model: Any,
    upsell_summary_text: str,
    dwh_request_suggestions: Dict[str, Any],
    workspace_id: str = "",
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
        "workspace_id": workspace_id,
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
            if workspace_id:
                state["workspace_id"] = workspace_id
        actions = getattr(tool_context, "actions", None)
        if actions is not None:
            actions.skip_summarization = True

    website_exploration = None
    if workspace_id:
        website_exploration = {
            "workspace_id": workspace_id,
            "is_b2b": _infer_is_b2b(business_model),
            "plg_type": _infer_plg_type(business_model),
            "website_url": website_url,
            "product_description": product,
            "icp_description": icp,
            "pricing_model": pricing_model,
            "product_assumptions": assumptions or [],
        }
        env = inspector_env()
        if env["agent_secret"]:
            try:
                result = inspector_post(
                    env["url"],
                    "/api/agent/data/website-exploration",
                    env["agent_secret"],
                    env["vercel_protection"],
                    website_exploration,
                    timeout=60,
                    include_vercel=True,
                )
                if tool_context is not None:
                    state = getattr(tool_context, "state", None)
                    if state is not None:
                        state["inspector_website_write"] = result
                        state["website_exploration"] = website_exploration
            except Exception as exc:
                if tool_context is not None:
                    state = getattr(tool_context, "state", None)
                    if state is not None:
                        state["inspector_website_write"] = {"status": "error", "error": str(exc)}
                        state["website_exploration"] = website_exploration
        elif tool_context is not None:
            state = getattr(tool_context, "state", None)
            if state is not None:
                state["inspector_website_write"] = {"status": "skipped", "reason": "missing_agent_secret"}
                state["website_exploration"] = website_exploration
    return {
        "ok": True,
        "website_url": data.get("website_url", ""),
        "workspace_id": data.get("workspace_id", ""),
        "business_model": summary.get("business_model", ""),
        "stored": True,
    }


def fetch_company_homepage(
    url: str,
    timeout_sec: int = 12,
    tool_context: Any = None,
) -> Dict[str, Any]:
    normalized_url = _normalize_website_url(url)
    cache_key = f"homepage_fetch:v1:url={normalized_url or url}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        if tool_context is not None:
            state = getattr(tool_context, "state", None)
            if state is not None:
                state["homepage_fetch"] = cached
        return cached

    try:
        resp = requests.get(normalized_url or url, timeout=timeout_sec)
        payload = {
            "status": "success",
            "url": normalized_url or url,
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type"),
            "text": _summarize_homepage_html(resp.text),
        }
    except Exception as exc:
        payload = {
            "status": "error",
            "url": normalized_url or url,
            "error": str(exc),
        }
    cache_write_json(cache_key, payload)
    if tool_context is not None:
        state = getattr(tool_context, "state", None)
        if state is not None:
            state["homepage_fetch"] = payload
    return payload


def emit_pipeline_report(tool_context: Any = None) -> Dict[str, Any]:
    state = getattr(tool_context, "state", {}) if tool_context is not None else {}
    signal_final_report = state.get("signal_final_report") if isinstance(state.get("signal_final_report"), dict) else {}
    signal_report = {}
    if signal_final_report:
        signal_report = signal_final_report
    elif isinstance(state.get("experiment_report"), dict):
        signal_report = {
            "ok": True,
            "run_id": state["experiment_report"].get("run_id"),
            "promoted_signals": state.get("promoted_signals") or [],
            "experiment_report": state["experiment_report"],
            "summary_path": signal_final_report.get("summary_path", ""),
        }

    payload = PipelineResult.model_validate(
        {
            "website_context": state.get("website_context") or {},
            "dwh_analytics": state.get("dwh_analytics") or {},
            "signal_report": signal_report,
            "promoted_signals": state.get("promoted_signals") or signal_report.get("promoted_signals") or [],
            "signal_objective": state.get("signal_objective") or {},
            "warehouse_profile": state.get("warehouse_profile") or {},
            "signal_run_id": str(state.get("signal_run_id") or ""),
            "summary_path": str(signal_report.get("summary_path") or signal_final_report.get("summary_path", "")),
            "pipeline_status": {
                "website_summary_ready": bool(state.get("website_context")),
                "dwh_summary_ready": bool(state.get("dwh_analytics")),
                "signal_report_ready": bool(signal_report),
                "inspector_website_write": state.get("inspector_website_write"),
                "inspector_write_summary": state.get("inspector_write_summary"),
            },
        }
    ).model_dump()

    if tool_context is not None:
        actions = getattr(tool_context, "actions", None)
        if actions is not None:
            actions.skip_summarization = True
        state["pipeline_report"] = payload
    return payload


from dwh_analyst.agent import root_agent as dwh_analyst
from signal_agent.agent import root_agent as signal_agent_root

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
        "You are the upsell_worker for the upsale opportunity system. The user provides a company website URL or bare domain.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior: never write, mutate, or delete data. Only read and summarize.\n"
        "- Use tools conservatively and explain missing data as assumptions.\n"
        "- Prefer the company's official website and avoid domain parking/sale pages or unrelated directories.\n"
        "- Never include secrets in your output (tokens, API keys).\n"
        "- Output JSON only. No Markdown, no code fences.\n"
        "\n"
        "Workflow:\n"
        "1) If the input includes a bare domain like example.com, treat it as a valid website input and use https://example.com.\n"
        "   Ask for a website only when neither a URL nor a plausible company domain is present.\n"
        "2) Call fetch_company_homepage to retrieve the front page HTML only.\n"
        "   Summarize what the company sells and how (B2B vs PLG, main product, ICP, pricing model).\n"
        "   If the page cannot be fetched, proceed using the URL host and explicit assumptions.\n"
        "3) Build a website context payload with: website_url, business_model, product, icp,\n"
        "   pricing_model, assumptions, upsell_summary_text, and dwh_request_suggestions.\n"
        "4) Extract the Inspector workspace_id from the input and pass it to emit_website_summary.\n"
        "5) Call emit_website_summary exactly once with that payload.\n"
        "   Do not leave fields blank; fill business_model, product, icp, and pricing_model from the homepage.\n"
        "   If pricing is unclear, add 2-4 assumptions about pricing/PLG motion instead of leaving fields empty.\n"
        "6) After the tool call, return only compact ack JSON with website_url, workspace_id, and stored=true.\n"
        "\n"
        "Tool input JSON for emit_website_summary (schema is provisional):\n"
        "{\n"
        "  \"website_url\": \"...\",\n"
        "  \"upsell_summary_text\": \"2-4 sentences summarizing the website + suggested DWH focus.\",\n"
        "  \"business_model\": \"...\",\n"
        "  \"product\": \"...\",\n"
        "  \"icp\": \"...\",\n"
        "  \"pricing_model\": \"...\",\n"
        "  \"assumptions\": [],\n"
        "  \"workspace_id\": \"...\",\n"
        "  \"dwh_request_suggestions\": {\"summary\": \"1-3 sentences covering the requested DWH follow-ups.\", \"items\": []}\n"
        "}\n"
        "\n"
        "If any tool is stubbed or unavailable, keep going and describe limitations in notes.\n"
    ),
)

pipeline_finalize_agent = Agent(
    model=MODEL,
    name="upsell_pipeline_finalize",
    description="Assembles the end-to-end upsell pipeline output.",
    generate_content_config=types.GenerateContentConfig(
        response_mime_type="application/json"
    ),
    tools=[emit_pipeline_report, *_SKILL_TOOLS],
    instruction=(
        "You have access to expert skill tools (list_skills, load_skill, load_skill_resource).\n"
        "Before finalizing, call list_skills once and load any skill that would help you frame\n"
        "the output. Do not load the same skill more than once.\n\n"
        "You finalize the stitched upsell pipeline output.\n"
        "Call emit_pipeline_report exactly once.\n"
        "Return JSON only.\n"
    ),
)

root_agent = SequentialAgent(
    name="upsell_pipeline",
    sub_agents=[upsell_worker, dwh_analyst, signal_agent_root, pipeline_finalize_agent],
)
