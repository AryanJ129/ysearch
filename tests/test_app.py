"""Streamlit AppTest — executes the real app script in-process.

A curl against the dev server only proves the server boots; the script itself
runs per browser session. AppTest runs it for real against a fresh db.
"""

import importlib.util

from streamlit.testing.v1 import AppTest

APP_PATH = importlib.util.find_spec("ysearch.app").origin


def test_app_runs_clean_on_empty_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # fresh cwd → fresh ysearch.db, no .env, no configs
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception
    assert at.title[0].value == "ysearch"
    # Empty-db Tracker + Funnel tabs show hints instead of crashing.
    info_texts = " ".join(str(block.value) for block in at.info)
    assert "No applications yet" in info_texts
    assert "funnel draws itself" in info_texts


def test_app_renders_scored_job_and_shortlist_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from ysearch import store, tracker
    from ysearch.models import Job

    conn = store.connect(tmp_path / "ysearch.db")
    store.init_db(conn)
    job_id, _ = store.upsert_job(
        conn,
        Job(source="jsearch", title="AI PM", company="Acme", url="https://x.example/1"),
        bucket="remote-india",
        key="acme|ai-pm|remote-india",
    )
    store.insert_score(
        conn, job_id, score=88, fit_reasons=["good"], flags=[], model="m", cost_usd=0.0
    )
    tracker.transition(conn, job_id, "shortlisted")
    conn.commit()
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception
    rendered = " ".join(str(m.value) for m in at.markdown)
    assert "88/100" in rendered and "AI PM" in rendered
    assert "shortlisted" in rendered  # state badge visible
    # Funnel metrics render with the seeded pipeline.
    metric_labels = [m.label for m in at.metric]
    assert "In pipeline" in metric_labels and "Response rate" in metric_labels
