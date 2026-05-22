import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

def load_jsonl(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
    return out

def _first_value(values: Any) -> Any:
    if not values:
        return None
    if isinstance(values, list) and values:
        return values[0]
    return values

def _get(record: Dict[str, Any], attr: str) -> Any:
    v = (record.get("values") or {}).get(attr)
    v0 = _first_value(v)
    if isinstance(v0, dict):
        # common patterns in Attio
        if "value" in v0:
            return v0["value"]
        if "domain" in v0:
            return v0["domain"]
        if "currency_value" in v0:
            return v0["currency_value"]
        if "option" in v0 and isinstance(v0["option"], dict):
            return v0["option"].get("title") or v0["option"].get("value")
        if "status" in v0 and isinstance(v0["status"], dict):
            return v0["status"].get("title") or v0["status"].get("value")
        if "interacted_at" in v0:
            return v0["interacted_at"]
    return v0

def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _days_ago(dt: datetime) -> float:
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0

def _safe_num(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _deal_company_id(deal: Dict[str, Any]) -> Optional[str]:
    assoc = (deal.get("values") or {}).get("associated_company")
    a0 = _first_value(assoc)
    if isinstance(a0, dict):
        # Attio record-reference shape varies; handle common cases
        if "target_record_id" in a0:
            return str(a0["target_record_id"])
        if "record_id" in a0:
            return str(a0["record_id"])
        if "id" in a0 and isinstance(a0["id"], dict) and "record_id" in a0["id"]:
            return str(a0["id"]["record_id"])
    if isinstance(a0, str):
        return a0
    return None

def score_company(company: Dict[str, Any], deals_for_company: List[Dict[str, Any]]) -> Tuple[float, Dict[str, float]]:
    contrib: Dict[str, float] = {}

    last_inter = _parse_dt(_get(company, "last_interaction"))
    next_inter = _parse_dt(_get(company, "next_interaction"))
    strength = _get(company, "strongest_connection_strength")

    est_arr = _safe_num(_get(company, "estimated_arr_usd"))
    annual_rev = _safe_num(_get(company, "annual_revenue"))
    funding = _safe_num(_get(company, "total_funding"))

    # Recency (0..30)
    if last_inter:
        d = _days_ago(last_inter)
        contrib["recency"] = max(0.0, 30.0 - min(30.0, d))
    else:
        contrib["recency"] = 0.0

    # Upcoming touch
    contrib["upcoming_touch"] = 10.0 if next_inter else 0.0

    # Warmth
    strength_map = {"high": 12.0, "medium": 7.0, "low": 2.0}
    s = str(strength).lower() if strength else ""
    contrib["connection"] = strength_map.get(s, 3.0)

    # Potential (light)
    contrib["est_arr"] = min(10.0, est_arr / 50_000.0)
    contrib["annual_rev"] = min(8.0, annual_rev / 200_000.0)
    contrib["funding"] = min(6.0, funding / 5_000_000.0)

    # Deal signals
    total_value = 0.0
    stage_bonus = 0.0
    for drec in deals_for_company:
        total_value += _safe_num(_get(drec, "value"))
        stage = str(_get(drec, "stage") or "").lower()
        if any(k in stage for k in ["negoti", "contract", "close", "won"]):
            stage_bonus = max(stage_bonus, 18.0)
        elif any(k in stage for k in ["demo", "trial", "pilot"]):
            stage_bonus = max(stage_bonus, 10.0)
        elif stage:
            stage_bonus = max(stage_bonus, 5.0)

    contrib["deal_stage"] = stage_bonus
    contrib["deal_value"] = min(16.0, total_value / 10_000.0)
    contrib["deal_count"] = min(6.0, float(len(deals_for_company)))

    score = sum(contrib.values())
    score = max(0.0, min(100.0, score))
    return score, contrib

def rank_accounts(
    companies_path: str = "data/companies.jsonl",
    deals_path: str = "data/deals.jsonl",
    top_n: int = 15
) -> dict:
    companies = load_jsonl(companies_path)
    deals = load_jsonl(deals_path)

    deals_by_company: Dict[str, List[Dict[str, Any]]] = {}
    for d in deals:
        cid = _deal_company_id(d)
        if cid:
            deals_by_company.setdefault(cid, []).append(d)

    ranked = []
    for c in companies:
        cid = str(_get(c, "record_id") or (c.get("id") or {}).get("record_id") or "")
        if not cid:
            continue
        name = _get(c, "name") or "(unnamed)"
        score, contrib = score_company(c, deals_by_company.get(cid, []))
        ranked.append({
            "company_id": cid,
            "name": name,
            "score": round(score, 2),
            "top_reasons": sorted(contrib.items(), key=lambda x: x[1], reverse=True)[:4],
            "deal_count": len(deals_by_company.get(cid, [])),
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return {"status": "success", "total_companies": len(ranked), "top": ranked[:top_n]}
