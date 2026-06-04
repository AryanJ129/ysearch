import sqlite3

import pytest

from ysearch import store


def test_wal_mode_is_on(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_busy_timeout_set(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()


def test_schema_creates_all_tables(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "scores", "applications", "state_events", "notes"} <= tables
    conn.close()


def test_dedupe_key_is_unique(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    insert = "INSERT INTO jobs (dedupe_key, title, company) VALUES (?, ?, ?)"
    conn.execute(insert, ("acme|pm|chennai", "PM", "Acme"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("acme|pm|chennai", "Product Manager", "ACME Inc"))
    conn.close()


def test_concurrent_connections_no_lock(tmp_path):
    """WAL rationale: a second connection (Streamlit rerun/tab) must read while
    another connection holds an open write transaction."""
    db = tmp_path / "t.db"
    writer = store.connect(db)
    store.init_db(writer)
    writer.execute(
        "INSERT INTO jobs (dedupe_key, title, company) VALUES ('k1', 'PM', 'Acme')"
    )  # uncommitted write txn open
    reader = store.connect(db)
    rows = reader.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert rows == 0  # snapshot before commit, and crucially: no 'database is locked'
    writer.commit()
    reader.close()
    writer.close()
