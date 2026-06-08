"""Process-wide observability wiring: AgentOps + Langfuse, side by side.

Initialized once at process start (and again, idempotently, at agent-import
time, since ADK loads each agent module directly without importing the
v0.0.2 package `__init__.py`). The function is idempotent and never raises —
observability failures must not block the agent pipeline.

The two backends are independent and run concurrently. Each is enabled purely
by the presence of its own credentials in the environment:

  - AgentOps  -> AGENTOPS_API_KEY
  - Langfuse  -> LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY (+ LANGFUSE_HOST)

Either, both, or neither may be active in a given deployment.

Langfuse is wired via OpenInference's GoogleADKInstrumentor, which patches
ADK's runtime to emit OTel spans for every model call and tool invocation.
Reference: https://langfuse.com/integrations/frameworks/google-adk
"""

from __future__ import annotations

import logging
import os
import threading

_LOG = logging.getLogger(__name__)
_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def init_observability() -> None:
    """Wire AgentOps + Langfuse if their credentials are present. Idempotent."""
    global _INITIALIZED
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        _INITIALIZED = True

    _init_agentops()
    _init_langfuse()


def _init_agentops() -> None:
    api_key = os.getenv("AGENTOPS_API_KEY")
    if not api_key:
        return
    try:
        import agentops

        agentops.init(api_key=api_key, default_tags=["google adk"])
        _LOG.info("AgentOps instrumentation active")
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

    # OpenInference doesn't capture token usage from LiteLLM-fronted models,
    # so we install our own callback that attaches usage + cost to the
    # currently-active OTel span.
    try:
        from .litellm_otel import register_litellm_otel_logger

        register_litellm_otel_logger()
    except Exception as exc:
        _LOG.warning("litellm OTel adapter registration failed: %s", exc)

    try:
        client = get_client()
        if client.auth_check():
            _LOG.info("Langfuse instrumentation active (host=%s)", base or "default")
        else:
            _LOG.warning("Langfuse auth_check failed — verify LANGFUSE_* env")
    except Exception as exc:
        _LOG.warning("Langfuse client check failed: %s", exc)
