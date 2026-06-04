"""Haiku scoring of unscored jobs — injection-guarded, cost-capped, telemetered.

Each job gets ONE cheap LLM call. Parse failures NEVER crash and NEVER trust:
the job is stored with score=null + needs_review. HTTP failures leave the job
unscored so the next run retries it.
"""

from __future__ import annotations

import json

import httpx

from ysearch import llm, prompts, store
from ysearch.config import Criteria

DAILY_CAP = 100


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


def apply_policy(score: int | None, flags: list[str]) -> int | None:
    """Deterministic scoring policy on top of the model's judgment.

    seniority_mismatch caps the score at 60 in CODE, not just in the prompt —
    a posting requiring far more experience must never top the inbox even if
    the model scores it high (the bug that put a 5-years-required role at 92
    for a 1.5-year owner)."""
    if score is not None and "seniority_mismatch" in flags:
        return min(score, 60)
    return score


def score_unscored(
    conn,
    criteria: Criteria,
    *,
    limit: int | None = None,
    daily_cap: int = DAILY_CAP,
    rescore_above: int | None = None,
) -> dict:
    """Score jobs without a score row (JSearch-targeted first), capped per day.

    rescore_above=N re-judges jobs whose LATEST score >= N instead (appends new
    score rows; latest wins) — used after criteria/prompt changes."""
    remaining = max(0, daily_cap - store.scores_today(conn))
    budget = min(limit, remaining) if limit is not None else remaining
    if rescore_above is not None:
        rows = store.jobs_with_latest_score_at_least(conn, rescore_above, limit=budget)
    else:
        rows = store.unscored_jobs(conn, limit=budget)
    criteria_text = criteria.as_prompt_text()
    scored = failed = 0
    spent = 0.0
    for row in rows:
        user_prompt = prompts.build_score_user_prompt(criteria_text, prompts.render_posting(row))
        try:
            text, cost = llm.chat(prompts.SCORE_SYSTEM, user_prompt)
        except (httpx.HTTPError, RuntimeError) as exc:
            # Leave unscored — the next run retries.
            print(f"  [!!] job {row['id']} ({row['title'][:40]!r}): {exc}")
            failed += 1
            continue
        score, reasons, flags = parse_reply(text)
        score = apply_policy(score, flags)
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
        llm.log_cost("score", job_id=row["id"], cost_usd=cost, score=score)
    conn.commit()
    return {
        "scored": scored,
        "failed": failed,
        "spent_usd": round(spent, 4),
        "cap_left": remaining - scored,
    }
