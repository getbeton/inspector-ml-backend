"""Integration-heavy tool surface for the v0.0.1 upsell agent."""

import os
import asyncio
from typing import Any, Dict, List, Tuple
from .connectors import (
    attio_list_companies,
    apollo_people_match_by_domain,
    supabase_fetch_account_metrics,
    posthog_fetch_group_activity_summary,
    stripe_fetch_customer_stub,
    get_cache_dir,
    attio_raw_company_query,
    supabase_auth_list_users
)
from .scoring import score_account
from .mock_data import mock_companies

def _env_present(key: str) -> bool:
    v = os.getenv(key)
    return bool(v and v.strip())

async def debug_attio_companies(limit: int) -> dict:
    return await attio_raw_company_query(limit=int(limit))

async def debug_supabase_auth_users(per_page: int = 200, max_users: int = 2000) -> dict:
    return await supabase_auth_list_users(mode="live", per_page=int(per_page), max_users=int(max_users))

async def debug_attio_cached(limit: int) -> dict:
    companies = await attio_list_companies(limit=int(limit))
    return {
        "count": len(companies),
        "sample": companies[:2],
        "cache_dir": str(get_cache_dir()),
    }


async def debug_apollo(domain: str) -> dict:
    return await apollo_people_match_by_domain(domain, mode="live")

async def preflight_status(mode: str, require_live_tokens: str) -> dict:
    """
    Check which integrations are configured. Never returns secrets, only booleans.
    Adds cache directory info so you can confirm caching is active.
    """
    require_live = require_live_tokens.lower().strip() == "true"

    cache_dir = get_cache_dir()
    try:
        cache_files = len([p for p in cache_dir.glob("*.json")])
    except Exception:
        cache_files = None

    status = {
        "mode": mode,
        "cache": {
            "dir": str(cache_dir),
            "files": cache_files,
            "policy": "trust_cache_until_manual_delete",
        },
        "gemini": {
            "google_api_key": _env_present("GOOGLE_API_KEY"),
            "vertex_ai": os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE",
        },
        "attio": {"configured": _env_present("ATTIO_API_TOKEN")},
        "apollo": {"configured": _env_present("APOLLO_API_KEY")},
        "supabase": {"configured": _env_present("SUPABASE_URL") and _env_present("SUPABASE_SERVICE_ROLE_KEY")},
        "posthog": {"configured": _env_present("POSTHOG_BASE_URL") and _env_present("POSTHOG_PROJECT_ID") and _env_present("POSTHOG_PERSONAL_API_KEY")},
        "stripe": {"configured": _env_present("STRIPE_SECRET_KEY")},
    }

    if mode == "live" and require_live:
        missing = [k for k, v in status.items() if isinstance(v, dict) and "configured" in v and not v["configured"]]
        status["ready_for_live"] = len(missing) == 0
        status["missing_for_live"] = missing
    else:
        status["ready_for_live"] = (mode != "live")

    return status


async def build_upsell_dataset(mode: str, top_n: int, lookback_days: int, min_score: float) -> dict:
    """
    End-to-end dataset builder:
      - Fetch candidates from Attio (or mock)
      - Enrich with Apollo / Supabase / PostHog (+ Stripe stub)
      - Score, filter, rank

    Connector-level caching ensures each API call happens at most once per unique key.
    """
    # 1) Load companies (cached inside connector)
    if mode == "mock":
        companies = mock_companies()
    else:
        companies = await attio_list_companies(limit=500)

    # 2) Pre-score quickly to pick enrichment shortlist
    base_scored: List[Tuple[float, dict]] = []
    for c in companies:
        s = score_account(
            account=c,
            usage=None,
            activity=None,
            enrichment=None,
            billing=None,
            lookback_days=lookback_days,
        )
        base_scored.append((s["score_0_1"], {**c, "_score": s}))

    base_scored.sort(key=lambda x: x[0], reverse=True)
    shortlist = [item[1] for item in base_scored[: max(top_n * 3, top_n)]]

    # 3) Enrich in parallel (each connector call is cached)
    async def enrich_one(acc: dict) -> dict:
        domain = acc.get("domain") or ""

        usage_task = supabase_fetch_account_metrics(domain, lookback_days, mode)
        activity_task = posthog_fetch_group_activity_summary(domain, lookback_days, mode)
        enrich_task = apollo_people_match_by_domain(domain, mode)
        billing_task = stripe_fetch_customer_stub(domain, mode)

        usage, activity, enrichment, billing = await asyncio.gather(
            usage_task, activity_task, enrich_task, billing_task, return_exceptions=True
        )

        def norm(x):
            if isinstance(x, Exception):
                return {"ok": False, "error": str(x)}
            return x

        usage = norm(usage)
        activity = norm(activity)
        enrichment = norm(enrichment)
        # If it has "raw", drop it to avoid huge outputs
        if isinstance(enrichment, dict) and "raw" in enrichment:
            enrichment = {k: v for k, v in enrichment.items() if k != "raw"}
        billing = norm(billing)

        score = score_account(
            account=acc,
            usage=usage,
            activity=activity,
            enrichment=enrichment,
            billing=billing,
            lookback_days=lookback_days,
        )

        return {
            **acc,
            "usage": usage,
            "activity": activity,
            "enrichment": enrichment,
            "billing": billing,
            "score": score,
        }

    enriched = await asyncio.gather(*[enrich_one(a) for a in shortlist])

    # 4) Filter + rank
    filtered_out = []
    kept = []

    for e in enriched:
        score01 = float(e["score"]["score_0_1"])
        reasons = []

        if not e.get("domain"):
            reasons.append("missing_domain")
        if not e.get("name"):
            reasons.append("missing_name")

        has_contact = bool(e.get("enrichment", {}).get("ok") and e["enrichment"].get("contacts"))
        has_owner = bool(e.get("owner_email"))

        # Soft requirement for MVP: allow ranking even without contact,
        # but mark it so sales knows it needs enrichment/manual work.
        if not (has_contact or has_owner):
            e["score"]["signals"]["needs_contact_research"] = True
            # do NOT filter out

        contacts = (e.get("enrichment", {}) or {}).get("contacts") or []
        has_actionable_contact = any(
            (c.get("email") or c.get("linkedin_url")) for c in contacts if isinstance(c, dict)
        )

        e["score"]["signals"]["has_actionable_contact"] = has_actionable_contact

        if score01 < float(min_score):
            reasons.append(f"score_below_threshold({score01:.2f}<{min_score:.2f})")

        explain = e["score"].get("explain", [])
        # If we filtered for missing_name, keep explain focused
        if "missing_name" in reasons:
            explain = [x for x in explain if "contact" not in x.lower()]

        if reasons:
            filtered_out.append({
                "name": e.get("name"),
                "domain": e.get("domain"),
                "score_0_1": score01,
                "reasons": reasons,
                "explain": e["score"].get("explain", []),
            })
        else:
            kept.append(e)

    kept.sort(key=lambda x: x["score"]["score_0_1"], reverse=True)
    kept = kept[:top_n]

    for k in kept:
        k["score"]["score_0_100"] = int(round(k["score"]["score_0_1"] * 100))

    return {
        "mode": mode,
        "lookback_days": lookback_days,
        "min_score": min_score,
        "ranked": kept,
        "filtered_out": filtered_out,
    }
