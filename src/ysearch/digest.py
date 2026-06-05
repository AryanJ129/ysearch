"""Markdown digest of the top scored jobs. Printed to stdout — personal job
data stays out of the repo (no digest files are written)."""

from __future__ import annotations

import datetime
import json

from ysearch import normalize, store


def render(conn, n: int = 10, *, now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows = store.top_scored(conn, n)
    if not rows:
        return "No scored jobs yet — run `ysearch scan` then `ysearch score`."
    lines = [f"# Top {len(rows)} scored jobs", ""]
    for row in rows:
        reasons = json.loads(row["fit_reasons"] or "[]")
        flags = json.loads(row["flags"] or "[]")
        flag_str = f" · flags: {', '.join(flags)}" if flags else ""
        age = normalize.posting_age_days(row["posted_at"], now=now)
        age_str = f" · posted {age}d ago" if age is not None else ""
        repost_str = f" · reposted ×{row['repost_count']}" if row["repost_count"] else ""
        lines += [
            f"## {row['score']}/100 — {row['title']} @ {row['company']}",
            f"{row['location'] or row['location_bucket']}{age_str}{repost_str}{flag_str}",
            *[f"- {reason}" for reason in reasons],
            f"Apply: {row['primary_url']}",
            "",
        ]
    return "\n".join(lines)
