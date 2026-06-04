"""SQLite store.

WAL mode is mandatory: Streamlit reruns the script on every interaction and can
run from multiple tabs — default journaling throws `database is locked` under
that pattern. Connections are short-lived and per-operation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ysearch.models import Job

DEFAULT_DB = Path("ysearch.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    location_bucket TEXT,
    remote INTEGER,
    salary_min REAL,
    salary_max REAL,
    currency TEXT,
    source_urls TEXT NOT NULL DEFAULT '[]',
    primary_url TEXT,
    posted_at TEXT,
    description TEXT DEFAULT '',
    raw_json TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scores (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    score INTEGER,
    fit_reasons TEXT,
    flags TEXT,
    model TEXT,
    cost_usd REAL,
    scored_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    state TEXT NOT NULL DEFAULT 'discovered',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    body_md TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# primary_url preference: direct ATS link > company page > aggregator chain.
_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com")
_AGGREGATOR_HOSTS = (
    "linkedin.",
    "indeed.",
    "glassdoor.",
    "bebee.",
    "jobgether.",
    "simplyhired.",
    "adzuna.",
    "instahyre.",
    "ventureloop.",
    "himalayas.app",
    "remoterocketship.",
    "simplify.jobs",
    "ixdf.org",
    "google.com",
)


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Open a connection with WAL + busy timeout set (see module docstring)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _url_rank(url: str) -> int:
    u = url.lower()
    if any(h in u for h in _ATS_HOSTS):
        return 0
    if any(h in u for h in _AGGREGATOR_HOSTS):
        return 2
    return 1  # likely a company-owned careers page


def choose_primary(urls: list[str]) -> str | None:
    return min(urls, key=_url_rank) if urls else None


def upsert_job(
    conn: sqlite3.Connection, job: Job, *, bucket: str, key: str, raw_json: str | None = None
) -> tuple[int, bool]:
    """Insert, or merge into the existing row for this dedupe_key.

    Merge policy (cross-source: JSearch + ATS see the same job with different
    URLs): union source_urls, re-choose primary (ATS link preferred), keep the
    LONGEST description, the EARLIEST posted_at, and fill null salary fields.
    Returns (job_id, was_new).
    """
    row = conn.execute("SELECT * FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
    if row is None:
        urls = [job.url] if job.url else []
        cur = conn.execute(
            """INSERT INTO jobs (dedupe_key, title, company, location, location_bucket,
                  remote, salary_min, salary_max, currency, source_urls, primary_url,
                  posted_at, description, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key,
                job.title,
                job.company,
                job.location,
                bucket,
                job.remote,
                job.salary_min,
                job.salary_max,
                job.currency,
                json.dumps(urls),
                choose_primary(urls),
                job.posted_at,
                job.description,
                raw_json,
            ),
        )
        return cur.lastrowid, True

    urls = set(json.loads(row["source_urls"]))
    if job.url:
        urls.add(job.url)
    url_list = sorted(urls)
    description = (
        job.description
        if len(job.description or "") > len(row["description"] or "")
        else row["description"]
    )
    posted_candidates = [p for p in (row["posted_at"], job.posted_at) if p]
    posted_at = min(posted_candidates) if posted_candidates else None
    conn.execute(
        """UPDATE jobs SET source_urls = ?, primary_url = ?, description = ?, posted_at = ?,
              salary_min = COALESCE(salary_min, ?), salary_max = COALESCE(salary_max, ?),
              currency = COALESCE(currency, ?)
           WHERE id = ?""",
        (
            json.dumps(url_list),
            choose_primary(url_list),
            description,
            posted_at,
            job.salary_min,
            job.salary_max,
            job.currency,
            row["id"],
        ),
    )
    return row["id"], False


def unscored_jobs(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    # JSearch rows first: they're query-targeted (the owner's actual searches);
    # ATS board fill scores after. Without this, a big ATS ingest starves the
    # targeted jobs out of the daily cap.
    sql = """SELECT j.* FROM jobs j LEFT JOIN scores s ON s.job_id = j.id
             WHERE s.job_id IS NULL
             ORDER BY (json_extract(j.raw_json, '$.source') = 'jsearch') DESC,
                      j.first_seen DESC"""
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def insert_score(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    score: int | None,
    fit_reasons: list[str],
    flags: list[str],
    model: str,
    cost_usd: float,
) -> None:
    conn.execute(
        "INSERT INTO scores (job_id, score, fit_reasons, flags, model, cost_usd)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, score, json.dumps(fit_reasons), json.dumps(flags), model, cost_usd),
    )


def scores_today(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM scores WHERE date(scored_at) = date('now')"
    ).fetchone()[0]


def top_scored(conn: sqlite3.Connection, n: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT j.*, s.score, s.fit_reasons, s.flags FROM jobs j
           JOIN scores s ON s.job_id = j.id
           WHERE s.score IS NOT NULL
           ORDER BY s.score DESC, j.first_seen DESC LIMIT ?""",
        (n,),
    ).fetchall()


def scored_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All jobs with a numeric score, best first (UI inbox)."""
    return conn.execute(
        """SELECT j.*, s.score, s.fit_reasons, s.flags FROM jobs j
           JOIN scores s ON s.job_id = j.id
           WHERE s.score IS NOT NULL
           ORDER BY s.score DESC, j.first_seen DESC"""
    ).fetchall()


def add_note(conn: sqlite3.Connection, job_id: int, body_md: str) -> None:
    conn.execute("INSERT INTO notes (job_id, body_md) VALUES (?, ?)", (job_id, body_md))
    conn.commit()


def notes_for_job(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notes WHERE job_id = ? ORDER BY created_at DESC, id DESC", (job_id,)
    ).fetchall()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
