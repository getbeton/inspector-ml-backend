"""Bolt LiteLLM token + cost usage onto the active OTel span.

Why this exists.
ADK + OpenInference's GoogleADKInstrumentor wraps each agent call in an
OTel span and Langfuse picks those spans up. But when the model is fronted
by `LiteLlm(model="gemini/...")`, the usage metadata returned by LiteLLM
never makes it onto the OTel span — Langfuse ends up with zero token
counts and zero cost. This module hooks LiteLLM's `CustomLogger` callback,
extracts `usage.{prompt,completion,total}_tokens` and `response_cost` from
the completion result, and sets them on whatever OTel span is currently
active using OpenInference / Langfuse-compatible attribute names.

Idempotent: `register_litellm_otel_logger()` only inserts the logger
once per process. Safe to call multiple times (mirrors the rest of
`observability.py`).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

_LOG = logging.getLogger(__name__)
_REGISTERED = False


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _set_span_token_attrs(
    *,
    model: Optional[str],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    total_tokens: Optional[int],
    cost_usd: Optional[float],
) -> None:
    """Attach token + cost attributes to the currently-active OTel span.

    Uses both OpenInference (`llm.token_count.*`) and OTel GenAI semconv
    (`gen_ai.usage.*`) names so Langfuse's parser picks them up regardless
    of which convention it favours.
    """
    try:
        from opentelemetry import trace
    except Exception as exc:  # pragma: no cover
        _LOG.debug("opentelemetry not available: %s", exc)
        return

    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return

    if model:
        span.set_attribute("llm.model_name", model)
        span.set_attribute("gen_ai.response.model", model)

    if prompt_tokens is not None:
        span.set_attribute("llm.token_count.prompt", prompt_tokens)
        span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
        span.set_attribute("gen_ai.usage.prompt_tokens", prompt_tokens)
    if completion_tokens is not None:
        span.set_attribute("llm.token_count.completion", completion_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
        span.set_attribute("gen_ai.usage.completion_tokens", completion_tokens)
    if total_tokens is not None:
        span.set_attribute("llm.token_count.total", total_tokens)

    if cost_usd is not None:
        # Langfuse-specific aliases: total_cost is what the UI surfaces.
        span.set_attribute("langfuse.observation.cost_details", str(cost_usd))
        span.set_attribute("gen_ai.usage.cost", cost_usd)


def _extract_usage(response_obj: Any) -> dict:
    """Return `{model, prompt_tokens, completion_tokens, total_tokens, cost}` from
    a LiteLLM ModelResponse / response_obj. Tolerates dict, pydantic, or
    attribute-style access. Returns {} when nothing usable is found.
    """
    if response_obj is None:
        return {}

    def _get(o: Any, key: str) -> Any:
        if o is None:
            return None
        if isinstance(o, dict):
            return o.get(key)
        return getattr(o, key, None)

    usage = _get(response_obj, "usage")
    if usage is None:
        # Some LiteLLM responses bury usage under hidden_params or _response_ms.
        hidden = _get(response_obj, "_hidden_params")
        if hidden is not None:
            usage = _get(hidden, "usage")

    prompt = _get(usage, "prompt_tokens")
    completion = _get(usage, "completion_tokens")
    total = _get(usage, "total_tokens")

    # response_cost is set by litellm.completion_cost or LiteLLM hidden params.
    hidden = _get(response_obj, "_hidden_params") or {}
    cost = (
        _get(hidden, "response_cost")
        or _get(response_obj, "response_cost")
    )
    if cost is None:
        try:
            from litellm import completion_cost
            cost = completion_cost(completion_response=response_obj)
        except Exception:  # pragma: no cover
            cost = None

    model = (
        _get(response_obj, "model")
        or _get(_get(response_obj, "model_response"), "model")
        or _get(hidden, "model")
    )

    return {
        "model": str(model) if model else None,
        "prompt_tokens": _to_int(prompt),
        "completion_tokens": _to_int(completion),
        "total_tokens": _to_int(total),
        "cost_usd": _to_float(cost),
    }


def register_litellm_otel_logger() -> None:
    """Install the OTel-bridging callback into LiteLLM. Idempotent."""
    global _REGISTERED
    if _REGISTERED:
        return

    try:
        import litellm
        from litellm.integrations.custom_logger import CustomLogger
    except Exception as exc:
        _LOG.warning("LiteLLM not importable, skipping OTel adapter: %s", exc)
        return

    class OtelUsageLogger(CustomLogger):  # type: ignore[misc]
        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            try:
                usage = _extract_usage(response_obj)
                if not any(usage.values()):
                    return
                _set_span_token_attrs(**usage)
            except Exception as exc:  # pragma: no cover
                _LOG.debug("OtelUsageLogger.log_success_event failed: %s", exc)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            self.log_success_event(kwargs, response_obj, start_time, end_time)

    logger = OtelUsageLogger()
    # `litellm.callbacks` is the canonical hook for CustomLogger instances
    # in modern LiteLLM (>=1.40). It runs for both sync + async completions.
    if logger not in (litellm.callbacks or []):
        litellm.callbacks = [*(litellm.callbacks or []), logger]
    _REGISTERED = True
    _LOG.info("LiteLLM OTel usage adapter registered")
