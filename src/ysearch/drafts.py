"""Cover-note drafts — grounded in config/resume.md, injection-guarded posting.

Drafts persist to the notes table so they survive reruns and feed the Phase-3
tracker view. The model is forbidden from inventing resume facts (DRAFT_SYSTEM).
"""

from __future__ import annotations

import re

from ysearch import llm, prompts, store

DRAFT_PREFIX = "### Cover-note draft"


def sanitize_note(text: str) -> str:
    """Deterministic plain-text guarantee for the SENDABLE note.

    Markdown bold, headers, and em-dashes read as obviously AI-written to a
    human recruiter — strip them in code even if the prompt slips."""
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def split_draft(body: str) -> tuple[str, str]:
    """Stored draft body → (sendable plain-text note, owner-only coaching).

    Strips the storage header line; older drafts without the delimiter come
    back as (whole note, "") and still get sanitized."""
    text = body
    if text.startswith(DRAFT_PREFIX):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if prompts.EMPHASIZE_DELIM in text:
        note, coaching = text.split(prompts.EMPHASIZE_DELIM, 1)
    else:
        note, coaching = text, ""
    return sanitize_note(note), coaching.strip()


def existing_draft(conn, job_id: int) -> str | None:
    """Latest saved draft for this job, if any."""
    for row in store.notes_for_job(conn, job_id):
        if row["body_md"].startswith(DRAFT_PREFIX):
            return row["body_md"]
    return None


def generate(conn, job_row, resume_text: str) -> tuple[str, float]:
    """One Haiku call → saved draft note. Returns (draft body, cost USD)."""
    user = prompts.build_draft_user_prompt(resume_text, prompts.render_posting(job_row))
    text, cost = llm.chat(prompts.DRAFT_SYSTEM, user, max_tokens=600)
    body = f"{DRAFT_PREFIX} — {job_row['title']} @ {job_row['company']}\n\n{text}"
    store.add_note(conn, job_row["id"], body)
    llm.log_cost("draft", job_id=job_row["id"], cost_usd=cost)
    return body, cost
