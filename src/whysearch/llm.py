"""OpenRouter model config + startup validation.

The slug uses a DOT — `anthropic/claude-haiku-4.5`. The dashed form
(`claude-haiku-4-5`) is NOT a valid OpenRouter model ID and fails at request
time, so the slug is validated against the live /models list at startup.
"""

from __future__ import annotations

import httpx

MODEL = "anthropic/claude-haiku-4.5"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def model_available(models_payload: dict, slug: str = MODEL) -> bool:
    """Check a GET /models payload for the configured slug."""
    return any(m.get("id") == slug for m in models_payload.get("data", []))


def validate_model_slug(slug: str = MODEL) -> None:
    """Raise with a clear message if the slug is missing on OpenRouter.

    /models is public — no API key needed.
    """
    resp = httpx.get(f"{OPENROUTER_BASE}/models", timeout=15.0)
    resp.raise_for_status()
    if not model_available(resp.json(), slug):
        raise RuntimeError(
            f"Model slug {slug!r} not found on OpenRouter — check"
            " https://openrouter.ai/models for the current ID."
        )
