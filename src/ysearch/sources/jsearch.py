"""JSearch adapter — Google for Jobs aggregation.

Routes: OpenWeb Ninja direct portal (default; free tier 200 req/month HARD cap)
or RapidAPI. Cadence must stay frugal — ~4 queries/day ≈ 120 req/month.

NOTE: Naukri under-syndicates to Google for Jobs, so JSearch covers Naukri only
partially/unreliably. The Naukri email parser (Phase 2) is the real Naukri path
— do not drop it on the assumption JSearch covers Naukri.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from ysearch.models import Job

PROVIDERS: dict[str, dict[str, str]] = {
    "openwebninja": {
        "base": "https://api.openwebninja.com/jsearch",
        "env": "OPENWEBNINJA_API_KEY",
    },
    "rapidapi": {
        "base": "https://jsearch.p.rapidapi.com",
        "env": "RAPIDAPI_KEY",
    },
}


@dataclass
class SearchResult:
    jobs: list[Job] = field(default_factory=list)
    quota_headers: dict[str, str] = field(default_factory=dict)
    # Untransformed API payload — diagnostic artifacts must capture the INPUT
    # of a transformation, not its output (the spike dumps this).
    raw: dict = field(default_factory=dict)


def current_provider() -> str:
    provider = os.environ.get("JSEARCH_PROVIDER", "openwebninja")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown JSEARCH_PROVIDER {provider!r} — use openwebninja or rapidapi")
    return provider


def api_key_for(provider: str) -> str | None:
    return os.environ.get(PROVIDERS[provider]["env"]) or None


def _headers(provider: str, api_key: str) -> dict[str, str]:
    if provider == "rapidapi":
        return {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    return {"x-api-key": api_key}


def parse_jobs(payload: dict) -> list[Job]:
    jobs: list[Job] = []
    for row in payload.get("data") or []:
        # job_location is the populated field on the direct portal ("Anywhere"
        # for remote jobs); job_city/job_state/job_country come back null there.
        city = row.get("job_city") or ""
        country = row.get("job_country") or ""
        location = row.get("job_location") or ", ".join(p for p in (city, country) if p) or None
        jobs.append(
            Job(
                source="jsearch",
                title=row.get("job_title") or "(untitled)",
                company=row.get("employer_name") or "(unknown)",
                location=location,
                remote=row.get("job_is_remote"),
                salary_min=row.get("job_min_salary"),
                salary_max=row.get("job_max_salary"),
                currency=row.get("job_salary_currency"),
                url=row.get("job_apply_link") or row.get("job_google_link") or "",
                posted_at=row.get("job_posted_at_datetime_utc"),
                description=row.get("job_description") or "",
            )
        )
    return jobs


def search(
    query: str,
    *,
    country: str = "in",
    remote: bool | None = None,
    page: int = 1,
    provider: str | None = None,
    timeout: float = 30.0,
) -> SearchResult:
    """One quota-counted request. Raises with a helpful message if no key is set."""
    provider = provider or current_provider()
    api_key = api_key_for(provider)
    if not api_key:
        raise RuntimeError(
            f"No API key in ${PROVIDERS[provider]['env']} — sign up for the free tier"
            " (openwebninja.com, no card) and set it in .env"
        )
    params: dict[str, str] = {
        "query": query,
        "page": str(page),
        "num_pages": "1",
        "country": country,
    }
    if remote:
        params["work_from_home"] = "true"
    resp = httpx.get(
        f"{PROVIDERS[provider]['base']}/search",
        params=params,
        headers=_headers(provider, api_key),
        timeout=timeout,
    )
    resp.raise_for_status()
    # NOTE: the direct portal sends no rate-limit headers — quota visibility is
    # dashboard-only, so cadence stays conservatively designed client-side.
    quota = {k: v for k, v in resp.headers.items() if "limit" in k.lower() or "quota" in k.lower()}
    payload = resp.json()
    return SearchResult(jobs=parse_jobs(payload), quota_headers=quota, raw=payload)
