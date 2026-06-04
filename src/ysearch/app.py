"""ysearch Streamlit app — Inbox (scored jobs) + Shortlist (drafts, applied).

Launch via `ysearch ui`. Reads/writes the same SQLite db as the CLI; WAL mode
makes concurrent CLI scans + UI reruns safe. Scan/score stay CLI-only so the
UI never spends quota or LLM budget without an explicit button press (drafts
are the one exception — each press is one Haiku call, ~$0.002).
"""

from __future__ import annotations

import json

import streamlit as st

from ysearch import config, drafts, store, tracker

st.set_page_config(page_title="ysearch", page_icon="🔭", layout="wide")
config.load_env()

conn = store.connect()
store.init_db(conn)

total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
total_scored = conn.execute(
    "SELECT COUNT(DISTINCT job_id) FROM scores WHERE score IS NOT NULL"
).fetchone()[0]
last_scan = store.get_meta(conn, "last_scan_at") or "never"

st.title("ysearch")
st.caption(
    f"{total_jobs} jobs · {total_scored} scored · last scan: {last_scan} — "
    "run `ysearch scan && ysearch score` from the CLI to refresh"
)


def _flags(row) -> list[str]:
    return json.loads(row["flags"] or "[]")


def _reasons(row) -> list[str]:
    return json.loads(row["fit_reasons"] or "[]")


def _job_header(row, state: str | None) -> None:
    state_badge = f" · `{state}`" if state and state != "discovered" else ""
    st.markdown(f"**{row['score']}/100 — {row['title']}** @ {row['company']}{state_badge}")
    flag_text = " · ".join(_flags(row))
    st.caption(
        f"{row['location'] or row['location_bucket']}" + (f" · {flag_text}" if flag_text else "")
    )


def _details(row) -> None:
    with st.expander("Details"):
        for reason in _reasons(row):
            st.markdown(f"- {reason}")
        if row["description"]:
            st.text(row["description"][:1500])
        urls = json.loads(row["source_urls"] or "[]")
        if len(urls) > 1:
            st.caption("Also seen at: " + " · ".join(u for u in urls if u != row["primary_url"]))


# --- Sidebar filters ---
with st.sidebar:
    st.header("Filters")
    min_score = st.slider("Min score", 0, 100, 60, step=5)
    all_buckets = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT location_bucket FROM jobs ORDER BY location_bucket"
        ).fetchall()
        if r[0]
    ]
    buckets = st.multiselect("Location buckets", all_buckets, default=[])
    st.caption("Empty bucket filter = all locations.")

tab_inbox, tab_shortlist = st.tabs(["Inbox", "Shortlist"])

with tab_inbox:
    rows = store.scored_jobs(conn)
    filtered = [
        r
        for r in rows
        if r["score"] >= min_score and (not buckets or r["location_bucket"] in buckets)
    ]
    st.caption(f"{len(filtered)} of {len(rows)} scored jobs (showing top 100)")
    for row in filtered[:100]:
        app_row = tracker.application_for_job(conn, row["id"])
        state = app_row["state"] if app_row else None
        with st.container(border=True):
            col_main, col_apply, col_action = st.columns([6, 2, 2])
            with col_main:
                _job_header(row, state)
            with col_apply:
                if row["primary_url"]:
                    st.link_button("Apply ↗", row["primary_url"])
            with col_action:
                if state in (None, "discovered"):
                    if st.button("Shortlist", key=f"shortlist-{row['id']}"):
                        tracker.transition(conn, row["id"], "shortlisted")
                        st.rerun()
                else:
                    st.write(f"✓ {state}")
            _details(row)

with tab_shortlist:
    apps = tracker.applications_with_jobs(
        conn, states=("shortlisted", "applied", "screen", "interview", "offer")
    )
    if not apps:
        st.info("Nothing shortlisted yet — hit Shortlist on an Inbox job.")
    for row in apps:
        with st.container(border=True):
            col_main, col_apply, col_action = st.columns([6, 2, 2])
            with col_main:
                st.markdown(f"**{row['title']}** @ {row['company']} · `{row['state']}`")
                st.caption(row["location"] or row["location_bucket"])
            with col_apply:
                if row["primary_url"]:
                    st.link_button("Apply ↗", row["primary_url"])
            with col_action:
                if row["state"] == "shortlisted":
                    if st.button("Mark applied", key=f"applied-{row['id']}"):
                        tracker.transition(conn, row["id"], "applied")
                        st.rerun()
            draft = drafts.existing_draft(conn, row["id"])
            if draft:
                st.text_area("Draft (copy from here)", draft, height=320, key=f"draft-{row['id']}")
                if st.button("Regenerate draft (~$0.002)", key=f"redraft-{row['id']}"):
                    with st.spinner("Drafting..."):
                        drafts.generate(conn, row, config.load_resume())
                    st.rerun()
            else:
                if st.button("Draft cover note (~$0.002)", key=f"draft-btn-{row['id']}"):
                    with st.spinner("Drafting..."):
                        drafts.generate(conn, row, config.load_resume())
                    st.rerun()

conn.close()
