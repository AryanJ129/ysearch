"""SQLite store.

WAL mode is mandatory: Streamlit reruns the script on every interaction and can
run from multiple tabs — default journaling throws `database is locked` under
that pattern. Connections are short-lived and per-operation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = Path("whysearch.db")

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
"""


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
