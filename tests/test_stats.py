from ysearch import store, tracker
from ysearch.models import Job


def _conn_with_jobs(tmp_path, n=3):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    ids = []
    for i in range(n):
        job_id, _ = store.upsert_job(
            conn,
            Job(source="jsearch", title=f"Job {i}", company="Acme", url=f"https://x.example/{i}"),
            bucket="remote-india",
            key=f"acme|job-{i}|remote-india",
        )
        ids.append(job_id)
    return conn, ids


def test_pipeline_counts(tmp_path):
    conn, ids = _conn_with_jobs(tmp_path)
    tracker.transition(conn, ids[0], "shortlisted")
    tracker.transition(conn, ids[1], "shortlisted")
    tracker.transition(conn, ids[1], "applied")
    counts = tracker.pipeline_counts(conn)
    assert counts == {"shortlisted": 1, "applied": 1}


def test_response_rate_none_before_any_application(tmp_path):
    conn, ids = _conn_with_jobs(tmp_path)
    tracker.transition(conn, ids[0], "shortlisted")
    assert tracker.response_rate(conn) is None


def test_response_rate_counts_rejection_as_response_not_ghosting(tmp_path):
    conn, ids = _conn_with_jobs(tmp_path)
    for job_id in ids:
        tracker.transition(conn, job_id, "applied")
    tracker.transition(conn, ids[0], "screen")  # response
    tracker.transition(conn, ids[1], "rejected")  # response (a human said no)
    tracker.transition(conn, ids[2], "ghosted")  # NOT a response
    assert tracker.response_rate(conn) == 2 / 3


def test_time_in_stage_averages_consecutive_events():
    events = [
        # app 1: 2 days discovered, 1 day shortlisted
        {"application_id": 1, "to_state": "discovered", "at": "2026-06-01 00:00:00"},
        {"application_id": 1, "to_state": "shortlisted", "at": "2026-06-03 00:00:00"},
        {"application_id": 1, "to_state": "applied", "at": "2026-06-04 00:00:00"},
        # app 2: 4 days discovered (open-ended applied stage not counted)
        {"application_id": 2, "to_state": "discovered", "at": "2026-06-01 00:00:00"},
        {"application_id": 2, "to_state": "shortlisted", "at": "2026-06-05 00:00:00"},
    ]
    stage_days = tracker.time_in_stage(events)
    assert stage_days["discovered"] == 3.0  # avg of 2 and 4
    assert stage_days["shortlisted"] == 1.0
    assert "applied" not in stage_days  # open-ended, never counted
