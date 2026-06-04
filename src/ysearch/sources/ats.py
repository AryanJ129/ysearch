"""ATS public job-board adapters — no API keys, no quotas, no scraping.

Phase 1 (spike): Greenhouse. Lever/Ashby land in Phase 2.
"""

from __future__ import annotations

import html
import re

import httpx

from ysearch.models import Job

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", html.unescape(text or "")).strip()


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
