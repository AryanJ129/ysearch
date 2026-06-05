"""Email → tracker status suggestions (the anti-manual-upkeep feature).

ATS/recruiter emails (rejections, interview invites, offers) land in one
Gmail label; each NEW email gets ONE cheap classification call and becomes a
SUGGESTION in the Tracker tab. The owner clicks Apply or Dismiss.

Principle: suggestions NEVER move state. Only tracker.transition() — behind
the owner's click — changes an application, so the Sankey funnel stays a
record of human decisions.

# TODO: validate against real ATS emails. The pipeline is built and tested
# against synthetic-but-realistic fixtures (tests/fixtures/) — once real
# status emails land in the labeled inbox, run `ysearch scan` and eyeball the
# suggestions; tune the matcher/prompt to the real formats then.

Defensive by design: a malformed email yields zero suggestions, never a
crashed scan; a failed classification leaves the email unprocessed so the
next scan retries it. Email contents are never printed or logged.
"""

from __future__ import annotations

import email
import email.message
import email.utils
import hashlib
import imaplib
import json
import os
import sqlite3

import httpx
from bs4 import BeautifulSoup

from ysearch import llm, prompts, store, tracker
from ysearch.sources.email_naukri import IMAP_HOST, html_from_message, imap_settings

DEFAULT_STATUS_LABEL = "ysearch-status"

# Email kind → suggested tracker state. confirmation/other map to nothing:
# an application acknowledgement is not a state change.
KIND_TO_STATE = {
    "rejection": "rejected",
    "screen_invite": "screen",
    "interview_invite": "interview",
    "offer": "offer",
}

# States where a status email can still arrive. Terminal states don't match —
# a rejection email for an already-rejected application is noise.
OPEN_STATES = tuple(s for s in tracker.STATES if s not in ("rejected", "ghosted", "withdrawn"))


def status_label() -> str:
    return os.environ.get("YSEARCH_IMAP_STATUS_LABEL") or DEFAULT_STATUS_LABEL


# --- email plumbing ---


def message_key(msg: email.message.Message) -> str:
    """Stable dedupe key: Message-ID when present, content hash otherwise."""
    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid
    blob = f"{msg.get('From', '')}|{msg.get('Subject', '')}|{msg.get('Date', '')}"
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def email_text(msg: email.message.Message) -> str:
    """Plain text of the email body — text/plain part preferred, HTML
    stripped via soup otherwise. Safe empty string on anything malformed."""
    try:
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        html = html_from_message(msg)
        if html:
            return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        pass
    return ""


def _email_date_iso(msg: email.message.Message) -> str | None:
    try:
        date = email.utils.parsedate_to_datetime(msg.get("Date"))
        return date.date().isoformat() if date else None
    except (TypeError, ValueError):
        return None


def fetch_messages(limit: int = 50) -> list[email.message.Message]:
    """Latest messages under the status label. Read-only — unread state and
    the messages themselves are never touched."""
    user, password, _ = imap_settings()
    if not (user and password):
        return []
    messages: list[email.message.Message] = []
    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        imap.login(user, password)
        status, _ = imap.select(f'"{status_label()}"', readonly=True)
        if status != "OK":
            return []
        _, data = imap.search(None, "ALL")
        for mid in (data[0] or b"").split()[-limit:]:
            _, msg_data = imap.fetch(mid, "(RFC822)")
            if msg_data and msg_data[0]:
                messages.append(email.message_from_bytes(msg_data[0][1]))
    return messages


# --- classification ---

_VALID_KINDS = frozenset(KIND_TO_STATE) | {"confirmation", "other"}


def parse_classification(text: str) -> tuple[str, str | None, float]:
    """Model reply → (kind, company_guess, confidence). Same defensive ladder
    as score.parse_reply: whole reply → first {...} slice → ("other", None, 0)."""
    candidates = [text]
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{") : text.rfind("}") + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        kind = data.get("kind")
        if kind not in _VALID_KINDS:
            kind = "other"
        company = data.get("company_guess")
        company = str(company).strip() if company else None
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return kind, company, confidence
    return "other", None, 0.0


# --- matching ---


def open_applications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return tracker.applications_with_jobs(conn, states=OPEN_STATES)


def match_application(
    conn: sqlite3.Connection, text_blob: str, company_guess: str | None
) -> sqlite3.Row | None:
    """Fuzzy company match: an open application's company name appearing in
    the email (From/Subject/body), or echoing the model's company guess.
    Applications come most-recent-first, so ties go to the latest one."""
    blob = text_blob.lower()
    guess = (company_guess or "").lower().strip()
    for app in open_applications(conn):
        company = (app["company"] or "").lower().strip()
        if len(company) < 3:  # too short to match meaningfully ("AI", "X")
            continue
        if company in blob or (guess and (company in guess or guess in company)):
            return app
    return None


# --- the pipeline ---


def process_messages(conn: sqlite3.Connection, messages: list) -> dict:
    """Classify each NEW message and write suggestions. Returns counts.

    Per-message failure policy: malformed content classifies as "other"
    (deterministic — reprocessing won't improve it, so it IS marked
    processed); a failed LLM call leaves the message UNprocessed so the next
    scan retries it.
    """
    new_emails = suggestions = 0
    for msg in messages:
        key = message_key(msg)
        seen = conn.execute(
            "SELECT 1 FROM processed_emails WHERE message_id = ?", (key,)
        ).fetchone()
        if seen:
            continue
        subject = (msg.get("Subject") or "").strip()
        sender = (msg.get("From") or "").strip()
        body = email_text(msg)
        blob = f"{sender}\n{subject}\n{body}"
        try:
            reply, cost = llm.chat(prompts.STATUS_SYSTEM, prompts.build_status_user_prompt(blob))
        except (httpx.HTTPError, RuntimeError):
            continue  # not marked processed — retried next scan
        llm.log_cost("statussync", job_id=0, cost_usd=cost)
        new_emails += 1
        kind, company_guess, confidence = parse_classification(reply)
        conn.execute("INSERT INTO processed_emails (message_id) VALUES (?)", (key,))
        suggested_state = KIND_TO_STATE.get(kind)
        if suggested_state is None:
            continue  # confirmation/marketing/other — processed, no suggestion
        app = match_application(conn, blob, company_guess)
        conn.execute(
            """INSERT INTO status_suggestions
                 (application_id, suggested_state, kind, company_guess,
                  email_subject, email_date, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                app["application_id"] if app else None,
                suggested_state,
                kind,
                company_guess,
                subject[:200],
                _email_date_iso(msg),
                confidence,
            ),
        )
        suggestions += 1
    conn.commit()
    return {"new_emails": new_emails, "suggestions": suggestions}


def run(conn: sqlite3.Connection, limit: int = 50) -> dict:
    return process_messages(conn, fetch_messages(limit))


# --- suggestion lifecycle (the Tracker panel's backend) ---


def pending_suggestions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT s.*, j.title, j.company, a.state AS current_state, a.job_id
           FROM status_suggestions s
           LEFT JOIN applications a ON a.id = s.application_id
           LEFT JOIN jobs j ON j.id = a.job_id
           WHERE s.resolution = 'pending'
           ORDER BY s.id DESC"""
    ).fetchall()


def apply_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> None:
    """Owner accepted: move the application through tracker.transition (the
    ONLY state-mover) and pin the evidence as a note."""
    sugg = conn.execute(
        """SELECT s.*, a.job_id FROM status_suggestions s
           JOIN applications a ON a.id = s.application_id
           WHERE s.id = ? AND s.resolution = 'pending'""",
        (suggestion_id,),
    ).fetchone()
    if sugg is None:  # unmatched, already resolved, or unknown — nothing to move
        return
    tracker.transition(conn, sugg["job_id"], sugg["suggested_state"])
    if sugg["source"] == "stalled":  # nudges.py auto-ghost — no email behind it
        note = f"No response — {sugg['email_subject']} → marked {sugg['suggested_state']}"
    else:
        note = (
            f'Status email ({sugg["email_date"] or "undated"}): "{sugg["email_subject"]}"'
            f" → {sugg['suggested_state']}"
        )
    store.add_note(conn, sugg["job_id"], note)
    conn.execute(
        "UPDATE status_suggestions SET resolution = 'accepted' WHERE id = ?", (suggestion_id,)
    )
    conn.commit()


def dismiss_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> None:
    conn.execute(
        "UPDATE status_suggestions SET resolution = 'dismissed' WHERE id = ?", (suggestion_id,)
    )
    conn.commit()
