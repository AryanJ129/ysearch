"""Location bucketing + dedupe keys.

dedupe_key = slug(company)|slug(title)|location_bucket — NEVER URL: the same
job arrives from JSearch (Google for Jobs) and the company's own ATS board
with different URLs.
"""

from __future__ import annotations

import datetime
import re

from ysearch.models import Job


def posting_age_days(posted_at_iso: str | None, *, now: datetime.datetime) -> int | None:
    """Days since the posting date — the ghost-job staleness signal.

    `now` is passed in (not read from the clock) for testability. Returns
    None on missing/unparseable dates: age is a display/filter signal and a
    bad source date must never crash rendering. Future-dated postings clamp
    to 0 (source clock skew, not negative age).
    """
    if not posted_at_iso:
        return None
    try:
        posted = datetime.datetime.fromisoformat(posted_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=datetime.timezone.utc)
    return max(0, (now - posted).days)


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
