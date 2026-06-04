"""Markdown digest of the top scored jobs. Printed to stdout — personal job
data stays out of the repo (no digest files are written)."""

from __future__ import annotations

import json

from ysearch import store


def render(conn, n: int = 10) -> str:
    rows = store.top_scored(conn, n)
    if not rows:
        return "No scored jobs yet — run `ysearch scan` then `ysearch score`."
    lines = [f"# Top {len(rows)} scored jobs", ""]
    for row in rows:
        reasons = json.loads(row["fit_reasons"] or "[]")
        flags = json.loads(row["flags"] or "[]")
        flag_str = f" · flags: {', '.join(flags)}" if flags else ""
        lines += [
            f"## {row['score']}/100 — {row['title']} @ {row['company']}",
            f"{row['location'] or row['location_bucket']}{flag_str}",
            *[f"- {reason}" for reason in reasons],
            f"Apply: {row['primary_url']}",
            "",
        ]
    return "\n".join(lines)
