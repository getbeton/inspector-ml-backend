"""Stop-gap factory used by all v0.0.2 agents.

This file was missing from `fcc1350` ("stitch together all agent..."), which
introduced `from shared.model_factory import build_gemini` in three agents
without committing the helper. Without it, every `/run` invocation in
production fails with ModuleNotFoundError before the agent loads.

Reconstructed from call sites:
    MODEL          = build_gemini("UPSELL_AGENT_MODEL", "gemini-3-flash-preview")
    MODEL          = build_gemini("DWH_ANALYST_MODEL", "gemini-3-flash-preview")
    MODEL          = build_gemini("SIGNAL_AGENT_MODEL", "gemini-3-flash-preview")
    REVIEWER_MODEL = build_gemini(...)

Replace with the intended implementation when @Sashmark97 reconciles.
"""

import os

from google.adk.models.google_llm import Gemini


def build_gemini(env_var: str, default_model: str) -> Gemini:
    """Construct a Gemini model, letting an env var override the default name."""
    return Gemini(model=os.getenv(env_var, default_model))
