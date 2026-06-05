"""Close-the-loop layer: follow-up nudges, auto-ghost suggestions, response
intelligence. Everything here is deterministic — no LLM anywhere."""

from __future__ import annotations

import datetime

from ysearch import nudges, statussync, store, tracker
from ysearch.models import Job

NOW = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _conn(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    return conn


def _job(**kw):
    base = dict(source="jsearch", title="AI PM", company="Acme")
    base.update(kw)
    return Job(**base)


def _ts(days_ago: float) -> str:
    return (NOW - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _seed_stalled(conn, *, company="Acme", key="k", state="applied", days_ago=31.0, **job_kw):
    """An application whose ONLY event is `state`, `days_ago` days back."""
    job_id, _ = store.upsert_job(conn, _job(company=company, **job_kw), bucket="b", key=key)
    app = tracker.get_or_create(conn, job_id)
    conn.execute("DELETE FROM state_events WHERE application_id = ?", (app["id"],))
    conn.execute(
        "INSERT INTO state_events (application_id, from_state, to_state, at) VALUES (?, NULL, ?, ?)",
        (app["id"], state, _ts(days_ago)),
    )
    conn.execute("UPDATE applications SET state = ? WHERE id = ?", (state, app["id"]))
    conn.commit()
    return job_id, app["id"]


# --- the source-column migration (real dbs already have the v2 table) ---


def test_status_suggestions_source_migration(tmp_path):
    conn = store.connect(tmp_path / "old.db")
    conn.executescript(
        """CREATE TABLE status_suggestions (
               id INTEGER PRIMARY KEY,
               application_id INTEGER,
               suggested_state TEXT,
               kind TEXT NOT NULL,
               company_guess TEXT,
               email_subject TEXT,
               email_date TEXT,
               confidence REAL,
               resolution TEXT NOT NULL DEFAULT 'pending',
               created_at TEXT NOT NULL DEFAULT (datetime('now'))
           );
           INSERT INTO status_suggestions (kind) VALUES ('rejection');"""
    )
    conn.commit()
    store.init_db(conn)  # migration point
    row = conn.execute("SELECT * FROM status_suggestions").fetchone()
    assert row["kind"] == "rejection"  # data survived
    assert row["source"] == "email"  # backfilled default


def test_jobs_source_backfill_from_primary_url(tmp_path):
    """Pre-v2.1 dbs have no jobs.source — the migration attributes existing
    rows by apply-URL host, and leaves URL-less rows honestly NULL."""
    conn = store.connect(tmp_path / "old.db")
    conn.executescript(
        """CREATE TABLE jobs (
               id INTEGER PRIMARY KEY,
               dedupe_key TEXT UNIQUE NOT NULL,
               title TEXT NOT NULL,
               company TEXT NOT NULL,
               primary_url TEXT
           );
           INSERT INTO jobs (dedupe_key, title, company, primary_url) VALUES
             ('k1', 'A', 'Acme', 'https://boards.greenhouse.io/acme/jobs/1'),
             ('k2', 'B', 'Beta', 'https://in.indeed.com/job/2'),
             ('k3', 'C', 'Gamma', NULL);"""
    )
    conn.commit()
    store.init_db(conn)
    sources = dict(conn.execute("SELECT dedupe_key, source FROM jobs"))
    assert sources == {"k1": "greenhouse", "k2": "jsearch", "k3": None}


# --- follow-up nudges (live-computed) ---


def test_follow_up_nudge_after_threshold(tmp_path):
    conn = _conn(tmp_path)
    _seed_stalled(conn, days_ago=15)
    (nudge,) = nudges.pending_nudges(conn, now=NOW)
    assert nudge["kind"] == "follow_up" and nudge["days"] == 15
    assert nudge["company"] == "Acme"


def test_no_nudge_before_threshold(tmp_path):
    conn = _conn(tmp_path)
    _seed_stalled(conn, days_ago=5)
    assert nudges.pending_nudges(conn, now=NOW) == []


def test_logged_follow_up_resets_the_clock(tmp_path):
    conn = _conn(tmp_path)
    job_id, _ = _seed_stalled(conn, days_ago=20)
    assert len(nudges.pending_nudges(conn, now=NOW)) == 1
    nudges.log_follow_up(conn, job_id)  # note created now
    assert nudges.pending_nudges(conn, now=NOW) == []
    # ...but the clock runs again from the follow-up, not forever.
    later = NOW + datetime.timedelta(days=nudges.FOLLOW_UP_DAYS + 1)
    assert len(nudges.pending_nudges(conn, now=later)) == 1


def test_shortlist_stall_nudge(tmp_path):
    conn = _conn(tmp_path)
    _seed_stalled(conn, state="shortlisted", days_ago=8)
    (nudge,) = nudges.pending_nudges(conn, now=NOW)
    assert nudge["kind"] == "apply_or_drop" and nudge["days"] == 8
    conn2 = _conn(tmp_path)  # fresh-but-same db; recent shortlist → silent
    conn.execute("UPDATE state_events SET at = ?", (_ts(3),))
    conn.commit()
    assert nudges.pending_nudges(conn2, now=NOW) == []


# --- auto-ghost suggestions ---


def test_ghost_suggestion_created_once_per_stall(tmp_path):
    conn = _conn(tmp_path)
    _, app_id = _seed_stalled(conn, days_ago=31)
    assert nudges.generate_stall_suggestions(conn, now=NOW) == 1
    sugg = statussync.pending_suggestions(conn)[0]
    assert sugg["source"] == "stalled"
    assert sugg["suggested_state"] == "ghosted"
    assert sugg["application_id"] == app_id
    assert "31d with no movement" in sugg["email_subject"]
    # Idempotent — the next scan does not stack a duplicate.
    assert nudges.generate_stall_suggestions(conn, now=NOW) == 0


def test_ghost_thresholds_and_states(tmp_path):
    conn = _conn(tmp_path)
    _seed_stalled(conn, key="k1", days_ago=20)  # applied, not yet 30d
    _seed_stalled(conn, company="Beta", key="k2", state="screen", days_ago=31)
    _seed_stalled(conn, company="Gamma", key="k3", state="shortlisted", days_ago=40)  # never
    assert nudges.generate_stall_suggestions(conn, now=NOW) == 1
    sugg = statussync.pending_suggestions(conn)[0]
    assert sugg["company"] == "Beta"  # only the stalled screen qualifies


def test_dismissal_holds_until_the_application_moves(tmp_path):
    conn = _conn(tmp_path)
    job_id, app_id = _seed_stalled(conn, days_ago=31)
    nudges.generate_stall_suggestions(conn, now=NOW)
    sugg = statussync.pending_suggestions(conn)[0]
    statussync.dismiss_suggestion(conn, sugg["id"])
    # Still stalled, still dismissed — never re-asks within the same episode.
    assert nudges.generate_stall_suggestions(conn, now=NOW) == 0
    # The application moves (a new event), then stalls ANOTHER 31 days → a
    # fresh suggestion is fair game. (Backdate the old suggestion so the
    # same-second test timestamps don't mask the episode boundary.)
    conn.execute("UPDATE status_suggestions SET created_at = ? WHERE id = ?", (_ts(31), sugg["id"]))
    tracker.transition(conn, job_id, "screen")  # event stamped now
    later = NOW + datetime.timedelta(days=31)
    assert nudges.generate_stall_suggestions(conn, now=later) == 1


def test_apply_ghost_suggestion_moves_state_with_honest_note(tmp_path):
    conn = _conn(tmp_path)
    job_id, _ = _seed_stalled(conn, days_ago=31)
    nudges.generate_stall_suggestions(conn, now=NOW)
    sugg = statussync.pending_suggestions(conn)[0]
    statussync.apply_suggestion(conn, sugg["id"])
    assert tracker.application_for_job(conn, job_id)["state"] == "ghosted"
    notes = [n["body_md"] for n in store.notes_for_job(conn, job_id)]
    assert any(n.startswith("No response") and "ghosted" in n for n in notes)


# --- response intelligence ---


def _event(conn, app_id, to_state, days_ago):
    conn.execute(
        "INSERT INTO state_events (application_id, from_state, to_state, at) VALUES (?, NULL, ?, ?)",
        (app_id, to_state, _ts(days_ago)),
    )


def test_response_by_source_counts_and_doctrine(tmp_path):
    conn = _conn(tmp_path)
    # jsearch: 2 applied, 1 got a screen → 1/2. greenhouse: 1 applied, 1
    # rejected → 1/1 (a rejection IS a response). ghosted is NOT a response.
    _, a1 = _seed_stalled(conn, key="k1", days_ago=10)
    _, a2 = _seed_stalled(conn, key="k2", company="Beta", days_ago=10)
    _, a3 = _seed_stalled(conn, key="k3", company="Gamma", source="greenhouse", days_ago=10)
    _event(conn, a1, "screen", 5)
    _event(conn, a3, "rejected", 4)
    _event(conn, a2, "ghosted", 1)
    conn.commit()
    rows = {r["source"]: r for r in tracker.response_by_source(conn)}
    assert rows["jsearch"] == {"source": "jsearch", "applied": 2, "responded": 1}
    assert rows["greenhouse"] == {"source": "greenhouse", "applied": 1, "responded": 1}


def test_median_days_to_response(tmp_path):
    conn = _conn(tmp_path)
    _, a1 = _seed_stalled(conn, key="k1", days_ago=10)
    _, a2 = _seed_stalled(conn, key="k2", company="Beta", days_ago=20)
    _event(conn, a1, "screen", 6)  # 4 days to response
    _event(conn, a2, "rejected", 10)  # 10 days to response
    conn.commit()
    assert tracker.median_days_to_response(conn) == 7.0  # median of [4, 10]


def test_median_none_without_responses(tmp_path):
    conn = _conn(tmp_path)
    _seed_stalled(conn, days_ago=10)
    assert tracker.median_days_to_response(conn) is None
