import json

from ysearch import store
from ysearch.models import Job


def _conn(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    return conn


def _job(**kw):
    base = dict(source="jsearch", title="AI PM", company="Acme", url="https://in.indeed.com/j/1")
    base.update(kw)
    return Job(**base)


def test_insert_then_merge_unions_urls_and_prefers_ats(tmp_path):
    conn = _conn(tmp_path)
    key = "acme|ai-pm|remote-india"
    _, new1 = store.upsert_job(conn, _job(), bucket="remote-india", key=key)
    _, new2 = store.upsert_job(
        conn,
        _job(source="greenhouse", url="https://boards.greenhouse.io/acme/jobs/1"),
        bucket="remote-india",
        key=key,
    )
    assert (new1, new2) == (True, False)
    row = conn.execute("SELECT * FROM jobs WHERE dedupe_key=?", (key,)).fetchone()
    urls = json.loads(row["source_urls"])
    assert len(urls) == 2
    assert row["primary_url"] == "https://boards.greenhouse.io/acme/jobs/1"  # ATS beats aggregator
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_merge_keeps_longest_description_and_earliest_date(tmp_path):
    conn = _conn(tmp_path)
    key = "k"
    store.upsert_job(conn, _job(description="short", posted_at="2026-06-02"), bucket="b", key=key)
    store.upsert_job(
        conn,
        _job(description="a much longer description body", posted_at="2026-05-28"),
        bucket="b",
        key=key,
    )
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["description"] == "a much longer description body"
    assert row["posted_at"] == "2026-05-28"


def test_merge_fills_null_salary_only(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_job(conn, _job(salary_min=1000000.0), bucket="b", key="k")
    store.upsert_job(conn, _job(salary_min=9999999.0, salary_max=2000000.0), bucket="b", key="k")
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["salary_min"] == 1000000.0  # existing value never overwritten
    assert row["salary_max"] == 2000000.0  # null filled


def test_meta_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    assert store.get_meta(conn, "rotation_cursor") is None
    store.set_meta(conn, "rotation_cursor", "3")
    store.set_meta(conn, "rotation_cursor", "5")
    assert store.get_meta(conn, "rotation_cursor") == "5"
