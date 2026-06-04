"""Location bucketing + dedupe keys.

dedupe_key = slug(company)|slug(title)|location_bucket — NEVER URL: the same
job arrives from JSearch (Google for Jobs) and the company's own ATS board
with different URLs.
"""

from __future__ import annotations

import re

from ysearch.models import Job

_CITY_ALIASES = {
    "bangalore": "bengaluru",
    "madras": "chennai",
    "gurgaon": "gurugram",
    "new delhi": "delhi",
    "bombay": "mumbai",
}
_REMOTE_TOKENS = ("remote", "anywhere", "work from home", "wfh", "distributed")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_LOC_SPLIT_RE = re.compile(r"[,;/|]| - ")


def slugify(text: str | None) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-")


def location_bucket(
    location: str | None, *, remote: bool | None = None, country_hint: str | None = None
) -> str:
    """City-level bucket; remote jobs bucket as remote-india / remote-global.

    country_hint carries query context (JSearch remote results say just
    "Anywhere" — the `country=in` query param is what scopes them to India).
    """
    loc = (location or "").lower()
    if remote or any(token in loc for token in _REMOTE_TOKENS):
        if "india" in loc or (country_hint or "").lower() in ("in", "india"):
            return "remote-india"
        return "remote-global"
    if not loc:
        return "unknown"
    first = _LOC_SPLIT_RE.split(loc)[0].strip()
    first = _CITY_ALIASES.get(first, first)
    return slugify(first) or "unknown"


def dedupe_key(job: Job, bucket: str) -> str:
    return f"{slugify(job.company)}|{slugify(job.title)}|{bucket}"
