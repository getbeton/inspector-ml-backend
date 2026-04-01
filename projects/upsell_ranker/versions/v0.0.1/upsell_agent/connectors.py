import os
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import aiohttp

ATTIO_BASE = "https://api.attio.com"
APOLLO_BASE = "https://api.apollo.io"


# -------------------------
# Cache helpers (file-based)
# -------------------------

def get_cache_dir() -> Path:
    """
    Cache directory used by all connectors.
    Override with UPSALE_CACHE_DIR if desired.
    """
    custom = os.getenv("UPSALE_CACHE_DIR", "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
    else:
        # Default: package/.cache
        p = Path(__file__).resolve().parent / ".cache"

    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_key_to_path(cache_key: str) -> Path:
    # Hash to keep filenames short/safe across OS
    h = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return get_cache_dir() / f"{h}.json"


def cache_read_json(cache_key: str) -> Optional[Any]:
    path = _cache_key_to_path(cache_key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # If a cache file is corrupted, treat as miss (user can delete manually)
        return None


def cache_write_json(cache_key: str, payload: Any) -> None:
    path = _cache_key_to_path(cache_key)
    tmp = path.with_suffix(".tmp")

    # Atomic-ish write: write temp then rename
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _h_auth_bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}

async def attio_raw_company_query(limit: int) -> dict:
    token = os.getenv("ATTIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ATTIO_API_TOKEN missing")

    url = f"{ATTIO_BASE}/v2/objects/companies/records/query"
    payload = {"limit": min(int(limit), 50), "offset": 0}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers={**_h_auth_bearer(token), "Content-Type": "application/json"},
            json=payload,
        ) as r:
            data = await r.json()
            return {
                "http_status": r.status,
                "keys": list(data.keys()) if isinstance(data, dict) else str(type(data)),
                "data_len": len(data.get("data", [])) if isinstance(data, dict) else None,
                "sample": (data.get("data", [])[:2] if isinstance(data, dict) else None),
                "raw": data,
            }

# -------------------------
# Connectors (cached)
# -------------------------

async def attio_list_companies(limit: int) -> List[dict]:
    """
    Attio companies query:
      POST /v2/objects/companies/records/query

    Cache key includes limit.
    If cached, returns without requiring ATTIO_API_TOKEN.
    """
    limit = min(int(limit), 500)
    cache_key = f"attio_list_companies:v1:limit={limit}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, list):
        return cached

    token = os.getenv("ATTIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ATTIO_API_TOKEN missing (and no cache present)")

    url = f"{ATTIO_BASE}/v2/objects/companies/records/query"
    payload = {
        "limit": limit,
        "offset": 0,
        "filter": {"$and": []},  # match all
        # no sorts: don't accidentally exclude records missing "domains"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers={**_h_auth_bearer(token), "Content-Type": "application/json"},
            json=payload,
        ) as r:
            data = await r.json()
            if r.status >= 400:
                raise RuntimeError(f"Attio error {r.status}: {data}")

    # after getting data
    if not data.get("data"):
        # retry once with no filter field at all (some installs are picky)
        payload2 = {"limit": limit, "offset": 0}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers={**_h_auth_bearer(token), "Content-Type": "application/json"},
                                    json=payload2) as r:
                data = await r.json()
                if r.status >= 400:
                    raise RuntimeError(f"Attio error {r.status}: {data}")


    out: List[dict] = []
    for rec in data.get("data", []):
        values = rec.get("values", {}) or {}
        name = (values.get("name") or [{}])[0].get("value")
        domains = values.get("domains") or []
        domain = domains[0].get("domain") if domains else None

        out.append({
            "attio_record_id": rec.get("id", {}).get("record_id"),
            "name": name,
            "domain": domain,
            "web_url": rec.get("web_url"),
            # Optional fields you might map later:
            "arr": None,
            "seats": None,
            "plan": None,
            "owner_email": None,
            "stage": None,
        })

    cache_write_json(cache_key, out)
    return out


async def apollo_people_match_by_domain(domain: str, mode: str) -> dict:
    """
    Apollo match endpoint:
      POST /api/v1/people/match

    Cache key includes domain (and mode).
    In mock mode, returns mock data (not cached by default).
    """
    domain = (domain or "").strip().lower()

    if mode == "mock":
        return {
            "ok": True,
            "contacts": [{
                "name": "VP Ops",
                "title": "VP Operations",
                "email": f"vp.ops@{domain or 'example.com'}",
            }]
        }

    cache_key = f"apollo_people_match:v1:domain={domain}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return cached

    api_key = os.getenv("APOLLO_API_KEY", "").strip()
    if not api_key:
        # Do NOT cache “missing token” — let it work once token is provided
        return {"ok": False, "error": "APOLLO_API_KEY missing", "contacts": []}

    if not domain:
        return {"ok": False, "error": "no_domain", "contacts": []}

    url = f"{APOLLO_BASE}/api/v1/people/match"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }

    payload = {"domain": domain}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as r:
            data = await r.json()
            if r.status >= 400:
                return {"ok": False, "error": f"Apollo {r.status}: {data}", "contacts": []}

    person = data.get("person") or {}
    contacts = []

    if person:
        email = person.get("email")
        linkedin = person.get("linkedin_url")
        title = person.get("title")
        name = person.get("name") or " ".join([
            (person.get("first_name") or "").strip(),
            (person.get("last_name") or "").strip()
        ]).strip()

        actionable = any([
            email,
            linkedin,
            title,
            (name and name.strip()),
        ])

        if actionable:
            contacts.append({
                "name": name,
                "title": title,
                "email": email,
                "linkedin_url": linkedin,
            })

    result = {"ok": True, "contacts": contacts}
    # optionally keep a tiny org summary if useful:
    org = (person.get("organization") or {})
    org_summary = {
        "name": org.get("name"),
        "industry": org.get("industry"),
        "estimated_num_employees": org.get("estimated_num_employees"),
        "primary_domain": org.get("primary_domain"),
        "linkedin_url": org.get("linkedin_url"),
    }
    result = {"ok": True, "contacts": contacts, "org": org_summary}
    cache_write_json(cache_key, result)
    return result


def _parse_dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
    except: return None

async def supabase_auth_list_users(mode: str, per_page: int = 200, max_users: int = 5000) -> dict:
    """
    Fetch ALL auth users via Supabase Auth Admin API and cache the result.
    Requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY.

    Uses one cache key for the full user list so we don't re-download repeatedly.
    """
    if mode == "mock":
        return {"ok": True, "users": []}

    cache_key = f"supabase_auth_users:v1:per_page={per_page}:max_users={max_users}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict) and cached.get("ok") and isinstance(cached.get("users"), list):
        return cached

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        return {"ok": False, "error": "Supabase env missing"}

    # GoTrue Admin endpoint
    # Supabase docs recommend using admin methods with service_role. :contentReference[oaicite:1]{index=1}
    endpoint = f"{url}/auth/v1/admin/users"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    users: List[Dict[str, Any]] = []
    page = 1

    async with aiohttp.ClientSession() as session:
        while len(users) < max_users:
            params = {"page": page, "per_page": per_page}
            async with session.get(endpoint, headers=headers, params=params) as r:
                data = await r.json()
                if r.status >= 400:
                    return {"ok": False, "error": f"Supabase Auth Admin {r.status}: {data}"}

            batch = data.get("users", []) if isinstance(data, dict) else []
            if not batch:
                break

            # Keep only fields we need (smaller cache + faster downstream)
            for u in batch:
                users.append({
                    "id": u.get("id"),
                    "email": u.get("email"),
                    "created_at": u.get("created_at"),
                    "last_sign_in_at": u.get("last_sign_in_at"),
                    "app_metadata": u.get("app_metadata"),
                    "user_metadata": u.get("user_metadata"),
                    "identities": u.get("identities"),
                })

            page += 1

    result = {"ok": True, "users": users, "count": len(users)}
    cache_write_json(cache_key, result)
    return result


def supabase_auth_metrics_for_domain(auth_users_payload: dict, domain: str, lookback_days: int) -> dict:
    """
    Compute per-domain metrics from cached auth users.
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return {"ok": False, "error": "no_domain"}

    if not (isinstance(auth_users_payload, dict) and auth_users_payload.get("ok") and isinstance(
            auth_users_payload.get("users"), list)):
        return {
            "ok": False,
            "error": "auth_users_unavailable",
            "details": (auth_users_payload.get("error") if isinstance(auth_users_payload, dict) else str(
                type(auth_users_payload))),
        }

    now = datetime.now(timezone.utc)
    lb7 = now.timestamp() - 7 * 86400
    lb30 = now.timestamp() - 30 * 86400

    def email_domain(email: Optional[str]) -> str:
        if not email or "@" not in email:
            return ""
        return email.split("@", 1)[1].strip().lower()

    users = auth_users_payload["users"]
    scoped = [u for u in users if email_domain(u.get("email")) == domain]

    # timestamps
    created = [_parse_dt(u.get("created_at")) for u in scoped]
    last_signin = [_parse_dt(u.get("last_sign_in_at")) for u in scoped]

    new_7d = sum(1 for dt in created if dt and dt.timestamp() >= lb7)
    new_30d = sum(1 for dt in created if dt and dt.timestamp() >= lb30)
    active_7d = sum(1 for dt in last_signin if dt and dt.timestamp() >= lb7)
    active_30d = sum(1 for dt in last_signin if dt and dt.timestamp() >= lb30)

    # “Seat estimate” = unique users with that email domain
    seat_est = len(scoped)

    # No event tables yet → keep these neutral
    return {
        "ok": True,
        "seat_count_estimate": seat_est,
        "new_signups_7d": new_7d,
        "new_signups_30d": new_30d,
        "active_users_7d": active_7d,
        "active_users_30d": active_30d,
        "events_7d": 0,
        "events_30d": 0,
        "feature_limit_hits_7d": 0,
        "source": "supabase_auth_users",
    }

def supabase_identity_metrics_for_domain(rows: list[dict], domain: str) -> dict:
    now = datetime.now(timezone.utc)
    lb7 = now.timestamp() - 7*86400
    lb30 = now.timestamp() - 30*86400

    def dom(email: str) -> str:
        return email.split("@",1)[1].strip().lower() if email and "@" in email else ""

    scoped = [r for r in rows if dom(r.get("email")) == domain]

    created = [_parse_dt(r.get("created_at")) for r in scoped]
    last = [_parse_dt(r.get("last_sign_in_at")) for r in scoped]

    new_7d = sum(1 for dt in created if dt and dt.timestamp() >= lb7)
    new_30d = sum(1 for dt in created if dt and dt.timestamp() >= lb30)
    active_7d = sum(1 for dt in last if dt and dt.timestamp() >= lb7)
    active_30d = sum(1 for dt in last if dt and dt.timestamp() >= lb30)

    return {
        "ok": True,
        "source": "auth.identities via public.identity_signins",
        "seat_count_estimate": len({r.get("email") for r in scoped if r.get("email")}),
        "new_signups_7d": new_7d,
        "new_signups_30d": new_30d,
        "active_users_7d": active_7d,
        "active_users_30d": active_30d,
        "events_7d": 0,
        "events_30d": 0,
        "feature_limit_hits_7d": 0,
    }

async def supabase_fetch_account_metrics(domain: str, lookback_days: int, mode: str) -> dict:
    domain = (domain or "").strip().lower()
    lookback_days = int(lookback_days)

    if mode == "mock":
        return {
            "ok": True,
            "events_7d": 1200,
            "events_30d": 3200,
            "active_users_7d": 45,
            "active_users_30d": 70,
            "seat_count_estimate": 60,
            "feature_limit_hits_7d": 8,
            "source": "mock",
        }

    # Cache per-domain computed metrics (fast)
    cache_key = f"supabase_metrics:v3_identity_view:domain={domain}:lookback_days={lookback_days}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return cached

    if not domain:
        return {"ok": False, "error": "no_domain"}

    identities = await supabase_identity_signins_list(mode=mode)
    if not (identities.get("ok") and isinstance(identities.get("rows"), list)):
        return {"ok": False, "error": "identity_signins_unavailable", "details": identities.get("error")}

    metrics = supabase_identity_metrics_for_domain(identities["rows"], domain)
    cache_write_json(cache_key, metrics)
    return metrics

async def supabase_identity_signins_list(mode: str, page_size: int = 1000, max_rows: int = 200000) -> dict:
    if mode == "mock":
        return {"ok": True, "rows": [], "count": 0}

    cache_key = f"supabase_identity_signins:v1:page_size={page_size}:max_rows={max_rows}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict) and cached.get("ok") and isinstance(cached.get("rows"), list):
        return cached

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (url and key):
        return {"ok": False, "error": "Supabase env missing"}

    endpoint = f"{url}/rest/v1/identity_signins"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    rows = []
    offset = 0
    async with aiohttp.ClientSession() as session:
        while offset < max_rows:
            params = {
                "select": "email,created_at,last_sign_in_at",
                "limit": page_size,
                "offset": offset,
            }
            async with session.get(endpoint, headers=headers, params=params) as r:
                data = await r.json()
                if r.status >= 400:
                    return {"ok": False, "error": f"Supabase REST {r.status}: {data}"}
            if not isinstance(data, list) or not data:
                break
            rows.extend(data)
            offset += page_size

    result = {"ok": True, "rows": rows, "count": len(rows)}
    cache_write_json(cache_key, result)
    return result


async def posthog_fetch_group_activity_summary(domain: str, lookback_days: int, mode: str) -> dict:
    """
    PostHog summary stub.
    Cache key includes domain + lookback_days.

    When you implement live PostHog, keep the caching as-is:
      cache miss -> call Query API -> cache -> return
    """
    domain = (domain or "").strip().lower()
    lookback_days = int(lookback_days)

    if mode == "mock":
        return {
            "ok": True,
            "sessions_7d": 110,
            "sessions_30d": 260,
            "last_seen_days_ago": 1,
            "power_users": 3,
        }

    cache_key = f"posthog_group_activity:v1:domain={domain}:lookback_days={lookback_days}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return cached

    base = os.getenv("POSTHOG_BASE_URL", "").strip()
    project_id = os.getenv("POSTHOG_PROJECT_ID", "").strip()
    key = os.getenv("POSTHOG_PERSONAL_API_KEY", "").strip()
    if not (base and project_id and key):
        return {"ok": False, "error": "PostHog env missing"}

    if not domain:
        return {"ok": False, "error": "no_domain"}

    # TODO: implement PostHog Query API / HogQL here.
    result = {"ok": False, "error": "posthog_query_not_implemented_yet"}
    # Cache even stubbed result so you don’t re-call / re-process repeatedly.
    cache_write_json(cache_key, result)
    return result


async def stripe_fetch_customer_stub(domain: str, mode: str) -> dict:
    """
    Stripe stub (cached).
    """
    domain = (domain or "").strip().lower()

    if mode == "mock":
        return {"ok": True, "plan": "pro", "mrr": 1200, "renewal_days": 18, "trial_ends_days": None}

    cache_key = f"stripe_customer_stub:v1:domain={domain}"
    cached = cache_read_json(cache_key)
    if isinstance(cached, dict):
        return cached

    # Still stubbed; cache it so you don't keep "calling" it.
    result = {"ok": False, "error": "stripe_stub"}
    cache_write_json(cache_key, result)
    return result

