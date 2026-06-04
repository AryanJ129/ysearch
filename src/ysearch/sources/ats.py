"""ATS public job-board adapters — no API keys, no quotas, no scraping.

Greenhouse / Lever / Ashby all expose public JSON job-board endpoints.
"""

from __future__ import annotations

import html
import re

import httpx

from ysearch.models import Job

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", html.unescape(text or "")).strip()


# --- Greenhouse ---


def parse_greenhouse(slug: str, payload: dict) -> list[Job]:
    jobs: list[Job] = []
    for row in payload.get("jobs") or []:
        jobs.append(
            Job(
                source="greenhouse",
                title=row.get("title") or "(untitled)",
                company=slug,
                location=(row.get("location") or {}).get("name"),
                url=row.get("absolute_url") or "",
                posted_at=row.get("updated_at"),
                description=_strip_html(row.get("content") or ""),
            )
        )
    return jobs


def fetch_greenhouse(slug: str, *, timeout: float = 30.0) -> list[Job]:
    """Public board endpoint: boards-api.greenhouse.io/v1/boards/<slug>/jobs."""
    resp = httpx.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        params={"content": "true"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse_greenhouse(slug, resp.json())


# --- Lever ---


def parse_lever(slug: str, payload: list) -> list[Job]:
    jobs: list[Job] = []
    for row in payload or []:
        categories = row.get("categories") or {}
        created_ms = row.get("createdAt")
        posted_at = None
        if isinstance(created_ms, (int, float)):
            # Lever sends epoch milliseconds; keep ISO date for lexical compare.
            import datetime

            posted_at = datetime.datetime.fromtimestamp(
                created_ms / 1000, tz=datetime.timezone.utc
            ).isoformat()
        jobs.append(
            Job(
                source="lever",
                title=row.get("text") or "(untitled)",
                company=slug,
                location=categories.get("location"),
                url=row.get("hostedUrl") or "",
                posted_at=posted_at,
                description=row.get("descriptionPlain")
                or _strip_html(row.get("description") or ""),
            )
        )
    return jobs


def fetch_lever(slug: str, *, timeout: float = 30.0) -> list[Job]:
    """Public postings endpoint: api.lever.co/v0/postings/<slug>?mode=json."""
    resp = httpx.get(
        f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=timeout
    )
    resp.raise_for_status()
    return parse_lever(slug, resp.json())


# --- Ashby ---


def parse_ashby(slug: str, payload: dict) -> list[Job]:
    jobs: list[Job] = []
    for row in payload.get("jobs") or []:
        if row.get("isListed") is False:
            continue
        jobs.append(
            Job(
                source="ashby",
                title=row.get("title") or "(untitled)",
                company=slug,
                location=row.get("location"),
                remote=row.get("isRemote"),
                url=row.get("jobUrl") or row.get("applyUrl") or "",
                posted_at=row.get("publishedAt"),
                description=row.get("descriptionPlain")
                or _strip_html(row.get("descriptionHtml") or ""),
            )
        )
    return jobs


def fetch_ashby(slug: str, *, timeout: float = 30.0) -> list[Job]:
    """Public job-board endpoint: api.ashbyhq.com/posting-api/job-board/<slug>."""
    resp = httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=timeout)
    resp.raise_for_status()
    return parse_ashby(slug, resp.json())
