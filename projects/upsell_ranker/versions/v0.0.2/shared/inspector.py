import os
from typing import Any, Dict, Optional

import requests


def inspector_env() -> Dict[str, str]:
    return {
        "url": os.getenv("INSPECTOR_URL", "https://staging.getbeton.org").strip().rstrip("/"),
        "agent_secret": (
            os.getenv("INSPECTOR_AGENT_SECRET", "")
            or os.getenv("AGENT_SECRET", "")
        ).strip(),
        "vercel_protection": (
            os.getenv("INSPECTOR_VERCEL_PROTECTION", "")
            or os.getenv("VERCEL_PROTECTION", "")
        ).strip(),
    }


def inspector_headers(
    agent_secret: str,
    vercel_protection: str = "",
    include_vercel: bool = True,
) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "x-agent-secret": agent_secret,
    }
    if include_vercel and vercel_protection:
        headers["x-vercel-protection-bypass"] = vercel_protection
    return headers


def inspector_get(
    url: str,
    path: str,
    agent_secret: str,
    vercel_protection: str,
    params: Optional[dict] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    resp = requests.get(
        f"{url}{path}",
        headers=inspector_headers(agent_secret, vercel_protection, include_vercel=True),
        params=params or {},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def inspector_post(
    url: str,
    path: str,
    agent_secret: str,
    vercel_protection: str,
    payload: dict,
    timeout: int = 60,
    include_vercel: bool = True,
) -> Dict[str, Any]:
    resp = requests.post(
        f"{url}{path}",
        headers=inspector_headers(agent_secret, vercel_protection, include_vercel=include_vercel),
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
