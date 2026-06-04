"""OpenRouter access: model slug, validation, chat calls, cost telemetry.

The slug uses a DOT — `anthropic/claude-haiku-4.5`. The dashed form
(`claude-haiku-4-5`) is NOT a valid OpenRouter model ID and fails at request
time, so the slug is validated against the live /models list at startup.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import httpx

MODEL = "anthropic/claude-haiku-4.5"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
# Claude Haiku 4.5 via OpenRouter, USD per token.
PRICE_IN = 1e-6
PRICE_OUT = 5e-6
METRICS_PATH = Path("metrics/llm_costs.jsonl")


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


def mask_key(value: str | None) -> str:
    """Display fingerprint for a saved key: prefix + last 4, NEVER the full
    value — the full key must never be rendered or logged anywhere."""
    if not value:
        return "not set"
    if len(value) <= 12:
        return "…" + value[-2:]
    return f"{value[:6]}…{value[-4:]}"


def check_key(api_key: str | None = None) -> tuple[bool, str]:
    """Validate an OpenRouter key via GET /key — authenticated but FREE
    (no tokens spent). Returns (ok, human detail)."""
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return False, "no key set"
    try:
        resp = httpx.get(
            f"{OPENROUTER_BASE}/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        return False, f"network error: {exc}"
    if resp.status_code == 200:
        usage = (resp.json().get("data") or {}).get("usage")
        if isinstance(usage, (int, float)):
            return True, f"key valid — ${usage:.2f} used so far"
        return True, "key valid"
    if resp.status_code == 401:
        return False, "invalid key (401)"
    return False, f"unexpected response: HTTP {resp.status_code}"


def chat(
    system: str, user: str, *, max_tokens: int = 300, timeout: float = 60.0
) -> tuple[str, float]:
    """One chat call → (reply text, cost USD). Raises on HTTP/key problems."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set.")
    resp = httpx.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    usage = data.get("usage") or {}
    cost = usage.get("prompt_tokens", 0) * PRICE_IN + usage.get("completion_tokens", 0) * PRICE_OUT
    return text, cost


def log_cost(kind: str, *, job_id: int, cost_usd: float, score: int | None = None) -> None:
    """Append one spend entry to metrics/llm_costs.jsonl (gitignored)."""
    METRICS_PATH.parent.mkdir(exist_ok=True)
    entry: dict = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "job_id": job_id,
        "model": MODEL,
        "cost_usd": round(cost_usd, 6),
    }
    if score is not None:
        entry["score"] = score
    with METRICS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
