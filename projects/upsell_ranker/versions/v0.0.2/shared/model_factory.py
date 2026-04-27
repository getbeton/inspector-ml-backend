import os

from google.adk.models.google_llm import Gemini
from google.genai import types


def _to_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _to_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def build_gemini(model_env: str, default_model: str) -> Gemini:
    return Gemini(
        model=os.getenv(model_env, default_model),
        retry_options=types.HttpRetryOptions(
            attempts=_to_int_env("GEMINI_RETRY_ATTEMPTS", 6),
            initial_delay=_to_float_env("GEMINI_RETRY_INITIAL_DELAY_SEC", 1.0),
            max_delay=_to_float_env("GEMINI_RETRY_MAX_DELAY_SEC", 90.0),
            exp_base=_to_float_env("GEMINI_RETRY_EXP_BASE", 2.0),
            jitter=_to_float_env("GEMINI_RETRY_JITTER", 1.0),
            http_status_codes=[408, 429, 500, 502, 503, 504],
        ),
    )
