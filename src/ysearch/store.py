"""SQLite store.

WAL mode is mandatory: Streamlit reruns the script on every interaction and can
run from multiple tabs — default journaling throws `database is locked` under
that pattern. Connections are short-lived and per-operation.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

from ysearch import paths
from ysearch.models import Job

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
    repost_count INTEGER NOT NULL DEFAULT 0,
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


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with WAL + busy timeout set (see module docstring).

    Default resolves at call time (NOT def time) so the repo-vs-home data-dir
    decision sees the caller's actual cwd.
    """
    if db_path is None:
        db_path = paths.data_dir() / "ysearch.db"
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Column adds for dbs created before a column existed — CREATE TABLE IF
    NOT EXISTS never alters an existing table, so new columns need ALTERs."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "repost_count" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN repost_count INTEGER NOT NULL DEFAULT 0")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
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


# A re-listing this much newer than the stored posting date counts as a
# repost (taken down and re-posted) rather than source clock skew.
REPOST_THRESHOLD_DAYS = 14


def _parse_iso(value: str) -> datetime.datetime | None:
    """Defensive ISO-8601 parse; naive timestamps are assumed UTC."""
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _days_newer(stored_iso: str, incoming_iso: str) -> float | None:
    """How many days NEWER the incoming posted_at is vs the stored one.

    None when either date fails to parse — bad source dates must never crash
    a scan or fake a repost.
    """
    stored = _parse_iso(stored_iso)
    incoming = _parse_iso(incoming_iso)
    if stored is None or incoming is None:
        return None
    return (incoming - stored).total_seconds() / 86400


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
    # Ghost-job signal: the same job re-listed with a materially newer posting
    # date means it was taken down and re-posted. Keep the EARLIEST posted_at
    # (true age) but count the repost.
    repost_bump = 0
    if row["posted_at"] and job.posted_at:
        newer_days = _days_newer(row["posted_at"], job.posted_at)
        if newer_days is not None and newer_days > REPOST_THRESHOLD_DAYS:
            repost_bump = 1
    conn.execute(
        """UPDATE jobs SET source_urls = ?, primary_url = ?, description = ?, posted_at = ?,
              repost_count = repost_count + ?,
              salary_min = COALESCE(salary_min, ?), salary_max = COALESCE(salary_max, ?),
              currency = COALESCE(currency, ?)
           WHERE id = ?""",
        (
            json.dumps(url_list),
            choose_primary(url_list),
            description,
            posted_at,
            repost_bump,
            job.salary_min,
            job.salary_max,
            job.currency,
            row["id"],
        ),
    )
    return row["id"], False


def unscored_jobs(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    # Query-targeted rows first (JSearch searches + the user's own Naukri
    # alerts); ATS board fill scores after. Without this, a big ATS ingest
    # starves the targeted jobs out of the daily cap.
    sql = """SELECT j.* FROM jobs j LEFT JOIN scores s ON s.job_id = j.id
             WHERE s.job_id IS NULL
             ORDER BY (json_extract(j.raw_json, '$.source')
                       IN ('jsearch', 'naukri_email')) DESC,
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
        f"""SELECT j.*, s.score, s.fit_reasons, s.flags FROM jobs j
           {_LATEST_SCORE_JOIN}
           WHERE s.score IS NOT NULL
           ORDER BY s.score DESC, j.first_seen DESC LIMIT ?""",
        (n,),
    ).fetchall()


# Rescoring appends new score rows; the LATEST row per job is the verdict.
_LATEST_SCORE_JOIN = """
    JOIN (SELECT job_id, MAX(rowid) AS rid FROM scores GROUP BY job_id) latest
      ON latest.job_id = j.id
    JOIN scores s ON s.rowid = latest.rid
"""


def scored_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Jobs whose LATEST score is numeric, best first (UI inbox)."""
    return conn.execute(
        f"""SELECT j.*, s.score, s.fit_reasons, s.flags FROM jobs j
           {_LATEST_SCORE_JOIN}
           WHERE s.score IS NOT NULL
           ORDER BY s.score DESC, j.first_seen DESC"""
    ).fetchall()


def jobs_with_latest_score_at_least(
    conn: sqlite3.Connection, min_score: int, limit: int | None = None
) -> list[sqlite3.Row]:
    """Jobs whose latest score >= min_score — the rescore candidate set."""
    sql = f"""SELECT j.* FROM jobs j
              {_LATEST_SCORE_JOIN}
              WHERE s.score >= ? ORDER BY s.score DESC"""
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (min_score,)).fetchall()


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
