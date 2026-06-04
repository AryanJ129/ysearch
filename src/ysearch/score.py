"""Haiku scoring of unscored jobs — injection-guarded, cost-capped, telemetered.

Each job gets ONE cheap LLM call. Parse failures NEVER crash and NEVER trust:
the job is stored with score=null + needs_review. HTTP failures leave the job
unscored so the next run retries it.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import httpx

from ysearch import llm, prompts, store
from ysearch.config import Criteria

# Claude Haiku 4.5 via OpenRouter, USD per token.
PRICE_IN = 1e-6
PRICE_OUT = 5e-6
DAILY_CAP = 100
METRICS_PATH = Path("metrics/llm_costs.jsonl")


def parse_reply(text: str) -> tuple[int | None, list[str], list[str]]:
    """Whole-reply JSON → first {...} slice → (None, [], [needs_review])."""
    candidates = [text]
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{") : text.rfind("}") + 1])
    data = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            data = parsed
            break
    if data is None:
        return None, [], ["needs_review"]
    raw_score = data.get("score")
    score = max(0, min(100, int(raw_score))) if isinstance(raw_score, (int, float)) else None
    reasons = [str(r) for r in (data.get("fit_reasons") or []) if r][:4]
    flags = [f for f in (data.get("flags") or []) if f in prompts.ALLOWED_FLAGS]
    if score is None and "needs_review" not in flags:
        flags.append("needs_review")
    return score, reasons, flags


def _call_model(system: str, user: str, *, timeout: float = 60.0) -> tuple[str, float]:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set — scoring needs it.")
    resp = httpx.post(
        f"{llm.OPENROUTER_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": llm.MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 300,
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


def _posting_text(row) -> str:
    salary = (
        f"{row['salary_min']}–{row['salary_max']} {row['currency'] or ''}"
        if row["salary_min"] is not None or row["salary_max"] is not None
        else "not stated"
    )
    return (
        f"Title: {row['title']}\nCompany: {row['company']}\n"
        f"Location: {row['location'] or 'unknown'} (bucket: {row['location_bucket']})\n"
        f"Salary: {salary}\n\n{row['description'] or ''}"
    )


def _log_metric(entry: dict) -> None:
    METRICS_PATH.parent.mkdir(exist_ok=True)
    with METRICS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def score_unscored(
    conn, criteria: Criteria, *, limit: int | None = None, daily_cap: int = DAILY_CAP
) -> dict:
    """Score jobs without a score row, newest first, capped per day."""
    remaining = max(0, daily_cap - store.scores_today(conn))
    budget = min(limit, remaining) if limit is not None else remaining
    rows = store.unscored_jobs(conn, limit=budget)
    criteria_text = criteria.as_prompt_text()
    scored = failed = 0
    spent = 0.0
    for row in rows:
        user_prompt = prompts.build_score_user_prompt(criteria_text, _posting_text(row))
        try:
            text, cost = _call_model(prompts.SCORE_SYSTEM, user_prompt)
        except (httpx.HTTPError, RuntimeError) as exc:
            # Leave unscored — the next run retries.
            print(f"  [!!] job {row['id']} ({row['title'][:40]!r}): {exc}")
            failed += 1
            continue
        score, reasons, flags = parse_reply(text)
        store.insert_score(
            conn,
            row["id"],
            score=score,
            fit_reasons=reasons,
            flags=flags,
            model=llm.MODEL,
            cost_usd=cost,
        )
        spent += cost
        scored += 1
        _log_metric(
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "job_id": row["id"],
                "score": score,
                "model": llm.MODEL,
                "cost_usd": round(cost, 6),
            }
        )
    conn.commit()
    return {
        "scored": scored,
        "failed": failed,
        "spent_usd": round(spent, 4),
        "cap_left": remaining - scored,
    }
