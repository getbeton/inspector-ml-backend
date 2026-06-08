import os

from google.adk.models.lite_llm import LiteLlm

# Default provider assumed for bare model names (e.g. the legacy
# "gemini-3-flash-preview" still present in .env.example). LiteLLM requires a
# "<provider>/<model>" string, so anything without a "/" is routed to Gemini.
_DEFAULT_PROVIDER = "gemini"


def _to_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _normalize_model(model: str) -> str:
    """Return a LiteLLM-compatible "<provider>/<model>" string.

    Bare names (no "/") are assumed to be Gemini so existing env values like
    "gemini-3-flash-preview" keep working unchanged after the switch to
    LiteLLM. Fully-qualified strings (e.g. "anthropic/claude-opus-4-6",
    "vertex_ai/gemini-3-flash") pass through untouched.
    """
    model = model.strip()
    if "/" in model:
        return model
    return f"{_DEFAULT_PROVIDER}/{model}"


def _ensure_gemini_api_key() -> None:
    """LiteLLM's `gemini/` provider authenticates with GEMINI_API_KEY, while
    the rest of this codebase (and the native ADK Gemini client) uses
    GOOGLE_API_KEY. Mirror GOOGLE_API_KEY into GEMINI_API_KEY when the latter
    is unset so the litellm switch doesn't silently break auth.
    """
    if not os.getenv("GEMINI_API_KEY"):
        google_key = os.getenv("GOOGLE_API_KEY")
        if google_key:
            os.environ["GEMINI_API_KEY"] = google_key


def build_model(model_env: str, default_model: str) -> LiteLlm:
    """Build a LiteLLM-fronted model for an ADK agent.

    `model_env` is the env var holding the model id; `default_model` is used
    when it's unset. LiteLLM handles provider routing + retries, replacing the
    previous native-Gemini-only factory. `num_retries` is passed through to
    litellm.completion (configurable via LLM_NUM_RETRIES).
    """
    _ensure_gemini_api_key()
    model = _normalize_model(os.getenv(model_env, default_model))
    return LiteLlm(
        model=model,
        num_retries=_to_int_env("LLM_NUM_RETRIES", 6),
    )
