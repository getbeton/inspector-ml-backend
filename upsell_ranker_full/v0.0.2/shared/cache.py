import json
import hashlib
import os
from pathlib import Path
from typing import Any, Optional


def get_cache_dir() -> Path:
    """
    Cache directory used by v0.0.2 agents.
    Override with UPSALE_CACHE_DIR if desired.
    """
    custom = os.getenv("UPSALE_CACHE_DIR", "").strip()
    if custom:
        path = Path(custom).expanduser().resolve()
    else:
        path = Path(__file__).resolve().parent / ".cache"

    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key_to_path(cache_key: str) -> Path:
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    return get_cache_dir() / f"{digest}.json"


def cache_read_json(cache_key: str) -> Optional[Any]:
    path = _cache_key_to_path(cache_key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_write_json(cache_key: str, payload: Any) -> None:
    path = _cache_key_to_path(cache_key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)
