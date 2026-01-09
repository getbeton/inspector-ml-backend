from typing import Any, Dict, Optional

def _safe_num(x, default=0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def score_account(
    account: Dict[str, Any],
    usage: Optional[Dict[str, Any]],
    activity: Optional[Dict[str, Any]],
    enrichment: Optional[Dict[str, Any]],
    billing: Optional[Dict[str, Any]],
    lookback_days: int,
) -> Dict[str, Any]:
    """
    Returns:
      score_0_1: float
      explain: list[str]
      signals: dict[str, Any]
    """
    name = account.get("name") or ""
    domain = account.get("domain") or ""

    explain = []
    signals = {}

    # Usage growth signal (if present)
    events_7d = _safe_num((usage or {}).get("events_7d"), 0)
    events_30d = _safe_num((usage or {}).get("events_30d"), 0)
    au_7d = _safe_num((usage or {}).get("active_users_7d"), 0)
    au_30d = _safe_num((usage or {}).get("active_users_30d"), 0)
    limit_hits = _safe_num((usage or {}).get("feature_limit_hits_7d"), 0)

    growth = 0.0
    if events_30d > 0:
        # “recentness” proxy
        growth = _clamp01((events_7d * 4.0) / events_30d)  # ~1 means steady; >1 means accelerating, but clamped

    signals["usage_growth"] = growth
    signals["feature_limit_hits_7d"] = limit_hits

    # Activity recency
    last_seen_days = _safe_num((activity or {}).get("last_seen_days_ago"), 999)
    recency = _clamp01(1.0 - min(last_seen_days, 30.0) / 30.0)
    signals["recency"] = recency

    # “Expansion potential” proxy from seat estimate (or similar)
    seat_est = _safe_num((usage or {}).get("seat_count_estimate"), 0)
    seat_score = _clamp01(seat_est / 200.0)  # saturates at 200 seats
    signals["seat_score"] = seat_score

    # Billing pressure (renewal soon)
    renewal_days = _safe_num((billing or {}).get("renewal_days"), 999)
    renewal_pressure = _clamp01(1.0 - min(renewal_days, 60.0) / 60.0)
    signals["renewal_pressure"] = renewal_pressure

    # Contact availability
    has_contact = bool((enrichment or {}).get("ok") and (enrichment or {}).get("contacts"))
    signals["has_contact"] = has_contact

    # Org signal
    org_employees = _safe_num((enrichment or {}).get("org", {}).get("estimated_num_employees"), 0)
    org_size_score = _clamp01(org_employees / 200.0)
    signals["org_size_score"] = org_size_score

    # Supabase
    new_30d = _safe_num((usage or {}).get("new_signups_30d"), 0)
    signals["new_signups_30d"] = new_30d

    active_30 = _safe_num((usage or {}).get("active_users_30d"), 0)
    signals["active_users_30d"] = active_30

    # Weighted score
    score = 0.10
    score += 0.30 * growth
    score += 0.25 * recency
    score += 0.20 * seat_score
    score += 0.15 * _clamp01(limit_hits / 10.0)
    score += 0.10 * renewal_pressure
    score += 0.15 * org_size_score
    score += 0.05 * _clamp01(new_30d / 10.0)
    score += 0.20 * _clamp01(seat_est / 50.0)  # saturate at 50 seats
    score += 0.15 * _clamp01(active_30 / 30.0)  # saturate at 30 actives

    if has_contact:
        score += 0.05  # small boost

    score = _clamp01(score)

    # Explanations (human-readable)
    if growth > 0.75:
        explain.append("Usage is accelerating (strong recent activity vs 30d baseline).")
    elif growth > 0.50:
        explain.append("Usage is steady with meaningful recent activity.")
    elif growth > 0.25:
        explain.append("Some recent usage, but not strongly accelerating.")

    if recency > 0.66:
        explain.append("Very recently active — higher likelihood to engage with outreach now.")
    elif recency > 0.33:
        explain.append("Recently active — outreach should still land.")

    if seat_est >= 50:
        explain.append(f"Likely multi-seat account (~{int(seat_est)} seats) → expansion/seat upsell is plausible.")
    elif seat_est > 0:
        explain.append(f"Some team adoption (~{int(seat_est)} seats) → possible seat expansion.")

    if limit_hits >= 5:
        explain.append("Repeatedly hit product limits recently → strong trigger for upgrade conversation.")

    if renewal_days <= 30:
        explain.append("Renewal window is approaching → good timing for packaging/annual/upgrade discussions.")

    if org_employees >= 50:
        explain.append(f"Org size suggests expansion potential (~{int(org_employees)} employees).")

    if not domain:
        explain.append("Missing domain → enrichment + matching will be harder.")
    if not has_contact:
        explain.append("No enriched contact found yet → may require manual owner/contact mapping.")

    if new_30d >= 5:
        explain.append("New team members signed up recently → potential for seat expansion.")

    return {
        "score_0_1": score,
        "explain": explain,
        "signals": signals,
    }