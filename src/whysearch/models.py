"""Core data models."""

from __future__ import annotations

from pydantic import BaseModel


class Job(BaseModel):
    source: str  # jsearch | greenhouse | lever | ashby | naukri_email
    title: str
    company: str
    location: str | None = None
    remote: bool | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str | None = None
    url: str = ""
    posted_at: str | None = None  # ISO 8601 when known
    description: str = ""
