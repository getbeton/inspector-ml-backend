"""Process-wide observability wiring: AgentOps + Langfuse.

Initialized once at process start from `__init__.py` so the OTel instrumentor
is active before any ADK agent is constructed or run. The function is
idempotent and never raises — observability failures must not block the
agent pipeline.

Langfuse is wired via OpenInference's GoogleADKInstrumentor, which patches
ADK's runtime to emit OTel spans for every model call and tool invocation.
Spans are exported to Langfuse Cloud (or self-hosted) using the standard
LANGFUSE_* env vars. Reference:
https://langfuse.com/integrations/frameworks/google-adk
"""

from __future__ import annotations

import logging
import os
import threading

_LOG = logging.getLogger(__name__)
_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def init_observability() -> None:
    """Wire AgentOps + Langfuse if credentials are present in the environment."""
    global _INITIALIZED
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        _INITIALIZED = True

    _init_agentops()
    _init_langfuse()
    _log_memory_bank_fingerprints()


def _log_memory_bank_fingerprints() -> None:
    """Surface what memory-bank docs each agent role loaded. Useful for
    confirming, in production logs and Langfuse session tags, that the
    process saw the expected ground-truth content.
    """
    try:
        from shared.memory_bank import AGENT_DOC_PLAN, fingerprints_for
    except Exception as exc:
        _LOG.warning("memory-bank import failed: %s", exc)
        return
    for role in AGENT_DOC_PLAN:
        try:
            fps = fingerprints_for(role)
        except Exception as exc:
            _LOG.error("memory-bank load failed for role=%s: %s", role, exc)
            continue
        _LOG.info("memory-bank loaded role=%s docs=%s", role, fps)


def _init_agentops() -> None:
    api_key = os.getenv("AGENTOPS_API_KEY")
    if not api_key:
        return
    try:
        import agentops

        agentops.init(api_key=api_key, default_tags=["google adk", "mason"])
    except Exception as exc:
        _LOG.warning("AgentOps init failed: %s", exc)


def _init_langfuse() -> None:
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    if not (public_key and secret_key):
        return

    base = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")
    if base:
        os.environ["LANGFUSE_HOST"] = base
        os.environ.setdefault("LANGFUSE_BASE_URL", base)

    try:
        from langfuse import get_client
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor
    except ImportError as exc:
        _LOG.warning(
            "Langfuse deps missing (%s); install langfuse and "
            "openinference-instrumentation-google-adk to enable tracing",
            exc,
        )
        return

    try:
        GoogleADKInstrumentor().instrument()
    except Exception as exc:
        _LOG.warning("GoogleADKInstrumentor.instrument() failed: %s", exc)
        return

    try:
        client = get_client()
        if client.auth_check():
            _LOG.info("Langfuse instrumentation active (host=%s)", base or "default")
        else:
            _LOG.warning("Langfuse auth_check failed — verify LANGFUSE_* env")
    except Exception as exc:
        _LOG.warning("Langfuse client check failed: %s", exc)
