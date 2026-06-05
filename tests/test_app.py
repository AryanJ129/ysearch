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


def test_refresh_button_runs_scan_and_score(tmp_path, monkeypatch):
    """The in-app Scan + score button. All source toggles disabled → scan.run()
    touches no network, score finds nothing unscored — the full refresh path
    executes hermetically. Belt-and-braces: keys are stripped from the env so
    an accidental network path could only fail loudly, never spend."""
    monkeypatch.chdir(tmp_path)
    for key in ("OPENWEBNINJA_API_KEY", "OPENROUTER_API_KEY", "RAPIDAPI_KEY"):
        monkeypatch.delenv(key, raising=False)
    from ysearch import scan, store

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "criteria.yaml").write_text("queries: ['AI PM']\n")
    (tmp_path / "config" / "companies.yaml").write_text("greenhouse: []\n")
    conn = store.connect(tmp_path / "ysearch.db")
    store.init_db(conn)
    for source in scan.SOURCE_TOGGLES:
        scan.set_source_enabled(conn, source, False)  # commits (UI-rerun safe)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    at.button(key="refresh-btn").click().run()
    assert not at.exception
    # Scan ran for real: it stamped last_scan_at despite all sources being off.
    conn = store.connect(tmp_path / "ysearch.db")
    assert store.get_meta(conn, "last_scan_at") is not None
    conn.close()


def test_hide_stale_filter_and_age_badges(tmp_path, monkeypatch):
    """Ghost shield in the UI: ages render, the checkbox defaults OFF (flag,
    don't hide silently), and checking it hides only known->30d postings —
    unknown-age jobs always stay visible."""
    import datetime

    monkeypatch.chdir(tmp_path)
    from ysearch import store
    from ysearch.models import Job

    today = datetime.datetime.now(datetime.timezone.utc)
    fresh = (today - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    stale = (today - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
    conn = store.connect(tmp_path / "ysearch.db")
    store.init_db(conn)
    specs = [
        ("Fresh Role", fresh),
        ("Stale Role", stale),
        ("Undated Role", None),
    ]
    for title, posted in specs:
        job_id, _ = store.upsert_job(
            conn,
            Job(source="jsearch", title=title, company="Acme", posted_at=posted),
            bucket="remote-india",
            key=f"acme|{title.lower().replace(' ', '-')}|remote-india",
        )
        store.insert_score(conn, job_id, score=80, fit_reasons=[], flags=[], model="m", cost_usd=0)
    conn.commit()
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception
    rendered = " ".join(str(m.value) for m in at.markdown)
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Fresh Role" in rendered and "Stale Role" in rendered and "Undated Role" in rendered
    assert "posted 3d ago" in captions and "posted 60d ago" in captions

    stale_box = next(c for c in at.checkbox if c.label.startswith("Hide stale"))
    assert stale_box.value is False  # default OFF — flag, don't hide silently
    stale_box.check().run()
    assert not at.exception
    rendered = " ".join(str(m.value) for m in at.markdown)
    assert "Stale Role" not in rendered
    assert "Fresh Role" in rendered and "Undated Role" in rendered
