"""Phase-1 discovery spike — the gate before any downstream build.

Dumps raw results to spike/ (gitignored: it reveals target queries, salary
floor, and target companies) for the owner to eyeball. Good data → greenlight
Phase 2; thin → adjust the source stack first. Half a day in, not a week.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from whysearch import config
from whysearch.models import Job
from whysearch.sources import ats, jsearch

SPIKE_DIR = Path("spike")

# Crude title filter so an ATS board with hundreds of roles stays eyeball-able.
_ATS_TITLE_FILTER = re.compile(
    r"product|forward.deployed|solutions|automation|\bai\b|gtm|operations", re.IGNORECASE
)


def _fmt_salary(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return "—"
    lo = f"{job.salary_min:,.0f}" if job.salary_min is not None else "?"
    hi = f"{job.salary_max:,.0f}" if job.salary_max is not None else "?"
    return f"{lo}–{hi} {job.currency or ''}".strip()


def _cell(text: str, width: int) -> str:
    """Markdown-table-safe cell: literal pipes (e.g. Greenhouse multi-location
    strings) break columns."""
    return text[:width].replace("|", "/")


def _row(job: Job) -> str:
    return (
        f"| {job.source} | {_cell(job.title, 60)} | {_cell(job.company, 30)}"
        f" | {_cell(job.location or '—', 60)}"
        f" | {_fmt_salary(job)} | {len(job.description)} | {job.url} |"
    )


_TABLE_HEADER = (
    "| source | title | company | location | salary | desc chars | url |\n"
    "|---|---|---|---|---|---|---|"
)


def run(per_query: int = 10) -> int:
    config.load_env()
    criteria = config.load_criteria()
    companies = config.load_companies()
    SPIKE_DIR.mkdir(exist_ok=True)

    lines: list[str] = [
        "# Discovery spike — raw results for owner eyeball",
        "",
        "GATE: good data → greenlight Phase 2. Thin → adjust source stack first.",
        "",
    ]

    # --- JSearch (quota-counted: one request per query) ---
    provider = jsearch.current_provider()
    lines.append(f"## JSearch ({provider})")
    if jsearch.api_key_for(provider):
        for query in criteria.queries:
            try:
                result = jsearch.search(
                    query, country=criteria.country, remote=criteria.remote_ok or None
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                lines += ["", f"### {query}", f"FAILED: {exc}"]
                continue
            raw_path = SPIKE_DIR / f"jsearch_{re.sub(r'[^a-z0-9]+', '_', query.lower())}.json"
            raw_path.write_text(
                json.dumps([j.model_dump() for j in result.jobs], indent=2), encoding="utf-8"
            )
            lines += ["", f"### {query} — {len(result.jobs)} results", _TABLE_HEADER]
            lines += [_row(j) for j in result.jobs[:per_query]]
            if result.quota_headers:
                lines += ["", f"Quota headers: `{result.quota_headers}`"]
    else:
        env = jsearch.PROVIDERS[provider]["env"]
        lines.append(
            f"SKIPPED — no ${env} in .env. Sign up free (no card) at"
            " https://www.openwebninja.com/api/jsearch and re-run `whysearch spike`."
        )

    # --- Greenhouse ATS boards (free, unlimited) ---
    lines += ["", "## Greenhouse boards"]
    for slug in companies.greenhouse:
        try:
            jobs = ats.fetch_greenhouse(slug)
        except httpx.HTTPError as exc:
            lines += ["", f"### {slug}", f"FAILED (check slug): {exc}"]
            continue
        matching = [j for j in jobs if _ATS_TITLE_FILTER.search(j.title)]
        raw_path = SPIKE_DIR / f"greenhouse_{slug}.json"
        raw_path.write_text(json.dumps([j.model_dump() for j in jobs], indent=2), encoding="utf-8")
        lines += [
            "",
            f"### {slug} — {len(jobs)} roles total, {len(matching)} match the title filter",
            _TABLE_HEADER,
        ]
        lines += [_row(j) for j in matching[:per_query]]

    out = SPIKE_DIR / "results.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Spike dump written to {out} — eyeball it before greenlighting Phase 2.")
    return 0
