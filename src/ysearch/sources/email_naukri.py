"""Naukri job-alert email parser — the REAL Naukri path (IMAP, user's own inbox).

JSearch under-covers Naukri (it under-syndicates to Google for Jobs), so the
user's own alert emails are the coverage. Setup: create alerts on naukri.com,
Gmail filter → label (default `ysearch`), Gmail app password (2FA required).

# TODO: validate against a real Naukri alert email. The parser below is built
# and tested against a synthetic-but-realistic fixture (tests/fixtures/) —
# once a real alert lands in the labeled inbox, run `ysearch scan` and check
# the parsed rows; if fields are wrong, map the parser to the real format then.

Defensive by design: any field that can't be extracted gets a safe default;
a malformed email yields zero jobs, never a crashed scan. Email contents are
never printed or logged.
"""

from __future__ import annotations

import email
import email.message
import imaplib
import os
import re

from bs4 import BeautifulSoup

from ysearch.models import Job

IMAP_HOST = "imap.gmail.com"
DEFAULT_LABEL = "ysearch"
_JOB_LINK_RE = re.compile(r"job-listings", re.I)
_CARD_CLASS_RE = re.compile(r"jobTuple|job-card|jobCard", re.I)


def imap_settings() -> tuple[str | None, str | None, str]:
    return (
        os.environ.get("YSEARCH_IMAP_USER") or None,
        os.environ.get("YSEARCH_IMAP_PASSWORD") or None,
        os.environ.get("YSEARCH_IMAP_LABEL") or DEFAULT_LABEL,
    )


def have_creds() -> bool:
    user, password, _ = imap_settings()
    return bool(user and password)


def test_connection() -> tuple[bool, str]:
    """Login + label check only — never reads or reports email contents."""
    user, password, label = imap_settings()
    if not (user and password):
        return False, "Gmail address or app password not set"
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
            imap.login(user, password)
            status, _ = imap.select(f'"{label}"', readonly=True)
            if status == "OK":
                return True, f"Connected — found label '{label}'"
            return (
                False,
                f"Logged in, but label '{label}' not found — create the Gmail filter/label",
            )
    except imaplib.IMAP4.error:
        return False, "Login failed — check the app password (2FA + app password required)"
    except OSError as exc:
        return False, f"Network error: {exc}"


def is_naukri_sender(msg: email.message.Message) -> bool:
    return "naukri" in (msg.get("From") or "").lower()


def html_from_message(msg: email.message.Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    return ""


def _card_text(card, class_fragment: str) -> str | None:
    el = card.find(attrs={"class": re.compile(class_fragment, re.I)}) if card else None
    text = el.get_text(strip=True) if el else ""
    return text or None


def parse_alert_html(html: str, *, posted_at: str | None = None) -> list[Job]:
    """Job-listing links → Job rows. Safe defaults everywhere; never raises."""
    jobs: list[Job] = []
    soup = BeautifulSoup(html or "", "html.parser")
    for link in soup.find_all("a", href=_JOB_LINK_RE):
        url = link.get("href") or ""
        title = link.get_text(strip=True) or "(untitled)"
        card = link.find_parent(attrs={"class": _CARD_CLASS_RE}) or link.parent
        company = _card_text(card, "comp") or "(unknown)"
        location = _card_text(card, "loc")
        description = card.get_text(" ", strip=True)[:2000] if card else ""
        jobs.append(
            Job(
                source="naukri_email",
                title=title,
                company=company,
                location=location,
                url=url,
                posted_at=posted_at,
                description=description,
            )
        )
    return jobs


def parse_message(msg: email.message.Message) -> list[Job]:
    posted_at = None
    try:
        date = email.utils.parsedate_to_datetime(msg.get("Date"))
        posted_at = date.isoformat() if date else None
    except (TypeError, ValueError):
        pass
    return parse_alert_html(html_from_message(msg), posted_at=posted_at)


def fetch(limit: int = 50) -> list[Job]:
    """Fetch Naukri alerts under the configured label. Read-only: messages
    keep their unread state; re-parsed jobs simply dedupe-merge downstream."""
    user, password, label = imap_settings()
    if not (user and password):
        return []
    jobs: list[Job] = []
    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        imap.login(user, password)
        status, _ = imap.select(f'"{label}"', readonly=True)
        if status != "OK":
            return []
        _, data = imap.search(None, "ALL")
        message_ids = (data[0] or b"").split()[-limit:]
        for mid in message_ids:
            _, msg_data = imap.fetch(mid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            if is_naukri_sender(msg):
                jobs.extend(parse_message(msg))
    return jobs
