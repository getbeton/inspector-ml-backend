import os, json, time
from pathlib import Path
import requests
from typing import Any, Dict, List

ATTIO_BASE = "https://api.attio.com/v2"

ATTIO_LIMIT = int(os.environ.get("ATTIO_LIMIT", "200"))         # page size
ATTIO_MAX_PAGES = int(os.environ.get("ATTIO_MAX_PAGES", "5"))   # HARD CAP while debugging
ATTIO_CACHE_TTL_SEC = int(os.environ.get("ATTIO_CACHE_TTL_SEC", "900"))  # 15 min default

def _is_cache_fresh(path: str) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    age = time.time() - p.stat().st_mtime
    return age < ATTIO_CACHE_TTL_SEC

def _headers() -> Dict[str, str]:
    token = os.environ.get("ATTIO_TOKEN")
    if not token:
        raise RuntimeError("Missing ATTIO_TOKEN env var")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(f"{ATTIO_BASE}{path}", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def _paginate_records(object_slug: str, limit: int = ATTIO_LIMIT, max_pages: int = ATTIO_MAX_PAGES):
    out: List[Dict[str, Any]] = []
    offset = 0
    for _ in range(max_pages):
        data = _post(f"/objects/{object_slug}/records/query", {"limit": limit, "offset": offset})
        batch = data.get("data", [])
        out.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.12)  # be gentle with rate limits
    return out

def refresh_attio_dump(force: bool = False) -> dict:
    os.makedirs("data", exist_ok=True)
    c_path = "data/companies.jsonl"
    d_path = "data/deals.jsonl"

    # ✅ reuse cache unless forced
    if not force and _is_cache_fresh(c_path) and _is_cache_fresh(d_path):
        return {
            "status": "cached",
            "files": {"companies": c_path, "deals": d_path},
            "note": f"Using cached dumps (TTL={ATTIO_CACHE_TTL_SEC}s). Set force=True to refresh."
        }

    companies = _paginate_records("companies")
    deals = _paginate_records("deals")

    os.makedirs("data", exist_ok=True)
    c_path = "data/companies.jsonl"
    d_path = "data/deals.jsonl"

    with open(c_path, "w", encoding="utf-8") as f:
        for x in companies:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    with open(d_path, "w", encoding="utf-8") as f:
        for x in deals:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    return {
        "status": "success",
        "companies": len(companies),
        "deals": len(deals),
        "files": {"companies": c_path, "deals": d_path},
    }