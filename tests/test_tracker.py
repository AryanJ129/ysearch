import pytest

from ysearch import store, tracker
from ysearch.models import Job


def _conn_with_job(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    job_id, _ = store.upsert_job(
        conn,
        Job(source="jsearch", title="AI PM", company="Acme", url="https://x.example/1"),
        bucket="remote-india",
        key="acme|ai-pm|remote-india",
    )
    return conn, job_id


def test_get_or_create_records_initial_event(tmp_path):
    conn, job_id = _conn_with_job(tmp_path)
    app = tracker.get_or_create(conn, job_id)
    assert app["state"] == "discovered"
    events = conn.execute("SELECT * FROM state_events").fetchall()
    assert len(events) == 1
    assert events[0]["from_state"] is None
    assert events[0]["to_state"] == "discovered"
    # idempotent — no second application or event
    tracker.get_or_create(conn, job_id)
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM state_events").fetchone()[0] == 1


def test_transition_records_event_chain(tmp_path):
    conn, job_id = _conn_with_job(tmp_path)
    app = tracker.transition(conn, job_id, "shortlisted")
    assert app["state"] == "shortlisted"
    app = tracker.transition(conn, job_id, "applied")
    assert app["state"] == "applied"
    chain = [
        (e["from_state"], e["to_state"])
        for e in conn.execute("SELECT * FROM state_events ORDER BY id").fetchall()
    ]
    assert chain == [
        (None, "discovered"),
        ("discovered", "shortlisted"),
        ("shortlisted", "applied"),
    ]


def test_same_state_transition_is_noop(tmp_path):
    conn, job_id = _conn_with_job(tmp_path)
    tracker.transition(conn, job_id, "shortlisted")
    tracker.transition(conn, job_id, "shortlisted")
    assert conn.execute("SELECT COUNT(*) FROM state_events").fetchone()[0] == 2  # no dupe


def test_invalid_state_rejected(tmp_path):
    conn, job_id = _conn_with_job(tmp_path)
    with pytest.raises(ValueError, match="Unknown state"):
        tracker.transition(conn, job_id, "hired!!")


def test_applications_with_jobs_filters_by_state(tmp_path):
    conn, job_id = _conn_with_job(tmp_path)
    tracker.transition(conn, job_id, "shortlisted")
    assert len(tracker.applications_with_jobs(conn, states=("shortlisted",))) == 1
    assert len(tracker.applications_with_jobs(conn, states=("applied",))) == 0
    rows = tracker.applications_with_jobs(conn)
    assert rows[0]["title"] == "AI PM"
