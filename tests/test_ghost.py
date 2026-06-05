"""Ghost-job shields: schema migration, repost detection, posting age.

Signals are deterministic (age from posted_at, repost from merge behavior) —
no LLM judgement anywhere in this layer.
"""

from __future__ import annotations

import datetime

from ysearch import digest, normalize, prompts, store
from ysearch.models import Job

NOW = datetime.datetime(2026, 6, 5, 12, 0, tzinfo=datetime.timezone.utc)


def _conn(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    return conn


def _job(**kw):
    base = dict(source="jsearch", title="AI PM", company="Acme", url="https://in.indeed.com/j/1")
    base.update(kw)
    return Job(**base)


# --- migration ---

_OLD_SCHEMA = """
CREATE TABLE jobs (
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
"""


def test_migration_adds_repost_count_without_data_loss(tmp_path):
    """A db created before the column existed gains it on init_db, keeping
    every existing row — CREATE TABLE IF NOT EXISTS alone can't do this."""
    conn = store.connect(tmp_path / "old.db")
    conn.executescript(_OLD_SCHEMA)
    conn.execute("INSERT INTO jobs (dedupe_key, title, company) VALUES ('k1', 'AI PM', 'Acme')")
    conn.commit()
    store.init_db(conn)  # migration point
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["title"] == "AI PM"  # data survived
    assert row["repost_count"] == 0  # new column, defaulted
    store.init_db(conn)  # idempotent — second run must not raise


# --- repost detection ---


def test_repost_increments_only_on_materially_newer_date(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_job(conn, _job(posted_at="2026-05-01"), bucket="b", key="k")
    # 4 days newer: clock skew territory, NOT a repost.
    store.upsert_job(conn, _job(posted_at="2026-05-05"), bucket="b", key="k")
    assert conn.execute("SELECT repost_count FROM jobs").fetchone()[0] == 0
    # 30 days newer: taken down and re-listed.
    store.upsert_job(conn, _job(posted_at="2026-05-31"), bucket="b", key="k")
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["repost_count"] == 1
    assert row["posted_at"] == "2026-05-01"  # true age kept
    # Another re-list, counted again.
    store.upsert_job(conn, _job(posted_at="2026-07-01"), bucket="b", key="k")
    assert conn.execute("SELECT repost_count FROM jobs").fetchone()[0] == 2


def test_repost_not_counted_for_older_missing_or_bad_dates(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_job(conn, _job(posted_at="2026-06-01"), bucket="b", key="k")
    store.upsert_job(conn, _job(posted_at="2026-04-01"), bucket="b", key="k")  # older
    store.upsert_job(conn, _job(posted_at=None), bucket="b", key="k")  # missing
    store.upsert_job(conn, _job(posted_at="not-a-date"), bucket="b", key="k")  # bad
    assert conn.execute("SELECT repost_count FROM jobs").fetchone()[0] == 0


def test_days_newer_defensive_parse():
    assert store._days_newer("2026-05-01", "2026-05-31") == 30.0
    assert store._days_newer("garbage", "2026-05-31") is None
    assert store._days_newer("2026-05-01", "garbage") is None
    # Z-suffixed (the real JSearch format) vs naive — both parse, both UTC.
    assert store._days_newer("2026-05-01T00:00:00.000Z", "2026-05-31") == 30.0


# --- posting age ---


def test_posting_age_days():
    assert normalize.posting_age_days("2026-05-06", now=NOW) == 30
    assert normalize.posting_age_days("2026-05-14T00:00:00.000Z", now=NOW) == 22
    assert normalize.posting_age_days(None, now=NOW) is None
    assert normalize.posting_age_days("not-a-date", now=NOW) is None
    assert normalize.posting_age_days("2026-07-01", now=NOW) == 0  # future → clamp


# --- surfaces ---


def test_render_posting_includes_age_line(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_job(conn, _job(posted_at="2026-04-21"), bucket="b", key="k")
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert "Posted: 45 days ago" in prompts.render_posting(row, now=NOW)


def test_render_posting_omits_age_when_unknown(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_job(conn, _job(posted_at=None), bucket="b", key="k")
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert "Posted:" not in prompts.render_posting(row, now=NOW)


def test_digest_shows_age_and_reposts(tmp_path):
    conn = _conn(tmp_path)
    job_id, _ = store.upsert_job(conn, _job(posted_at="2026-04-21"), bucket="b", key="k")
    conn.execute("UPDATE jobs SET repost_count = 2")
    store.insert_score(conn, job_id, score=80, fit_reasons=[], flags=[], model="m", cost_usd=0.0)
    out = digest.render(conn, now=NOW)
    assert "posted 45d ago" in out
    assert "reposted ×2" in out
