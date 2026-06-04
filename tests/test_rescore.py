"""Latest-score-per-job semantics + the deterministic seniority cap."""

from ysearch import store
from ysearch.models import Job
from ysearch.score import apply_policy


def _conn_with_scored_job(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    job_id, _ = store.upsert_job(
        conn,
        Job(source="jsearch", title="FDE", company="Acme", url="https://x.example/1"),
        bucket="remote-india",
        key="acme|fde|remote-india",
    )
    return conn, job_id


def test_latest_score_wins_no_duplicates(tmp_path):
    """Rescoring appends rows; queries must show the job ONCE with the latest."""
    conn, job_id = _conn_with_scored_job(tmp_path)
    store.insert_score(conn, job_id, score=92, fit_reasons=[], flags=[], model="m", cost_usd=0)
    store.insert_score(
        conn, job_id, score=55, fit_reasons=[], flags=["seniority_mismatch"], model="m", cost_usd=0
    )
    rows = store.scored_jobs(conn)
    assert len(rows) == 1
    assert rows[0]["score"] == 55
    top = store.top_scored(conn, 10)
    assert len(top) == 1 and top[0]["score"] == 55


def test_rescore_candidates_use_latest_score(tmp_path):
    conn, job_id = _conn_with_scored_job(tmp_path)
    store.insert_score(conn, job_id, score=92, fit_reasons=[], flags=[], model="m", cost_usd=0)
    assert len(store.jobs_with_latest_score_at_least(conn, 60)) == 1
    store.insert_score(conn, job_id, score=40, fit_reasons=[], flags=[], model="m", cost_usd=0)
    # Latest is 40 → no longer a >=60 candidate, despite the old 92 row.
    assert store.jobs_with_latest_score_at_least(conn, 60) == []


def test_latest_null_score_hides_job_from_inbox(tmp_path):
    """If the newest verdict is needs_review (score null), the old numeric
    score must not resurrect the job."""
    conn, job_id = _conn_with_scored_job(tmp_path)
    store.insert_score(conn, job_id, score=80, fit_reasons=[], flags=[], model="m", cost_usd=0)
    store.insert_score(
        conn, job_id, score=None, fit_reasons=[], flags=["needs_review"], model="m", cost_usd=0
    )
    assert store.scored_jobs(conn) == []


def test_seniority_cap_is_deterministic():
    """The 92-for-a-5-years-role bug: seniority_mismatch caps at 60 in code."""
    assert apply_policy(92, ["seniority_mismatch"]) == 60
    assert apply_policy(45, ["seniority_mismatch"]) == 45  # below cap untouched
    assert apply_policy(92, ["salary_unknown"]) == 92  # other flags don't cap
    assert apply_policy(None, ["seniority_mismatch", "needs_review"]) is None
