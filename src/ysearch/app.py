"""ysearch Streamlit app — Inbox (scored jobs) · Tracker (pipeline) · Funnel (Sankey).

Launch via `ysearch ui`. Reads/writes the same SQLite db as the CLI; WAL mode
makes concurrent CLI scans + UI reruns safe. Scan/score stay CLI-only so the
UI never spends quota or LLM budget without an explicit button press (drafts
are the one exception — each press is one Haiku call, ~$0.002).
"""

from __future__ import annotations

import datetime
import json

import streamlit as st

from ysearch import config, drafts, normalize, sankey, store, tracker

# Postings older than this are widely treated as likely-ghost (see README).
STALE_DAYS = 30
# One clock read per rerun — every age in a pass is consistent.
_NOW = datetime.datetime.now(datetime.timezone.utc)

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
col_status, col_refresh = st.columns([4, 1], vertical_alignment="center")
with col_status:
    st.caption(
        f"{total_jobs} jobs · {total_scored} scored · last scan: {last_scan} — "
        "refresh here or run `ysearch scan && ysearch score` in a terminal"
    )
with col_refresh:
    refresh_clicked = st.button(
        "Scan + score",
        key="refresh-btn",
        help="Runs a full scan (~4 JSearch quota requests) then scores new jobs (~$0.003 each).",
    )
if refresh_clicked:
    # Runs BEFORE the tabs render, so this same pass shows the fresh data.
    import io
    from contextlib import redirect_stdout

    from ysearch import scan
    from ysearch import score as score_mod

    try:
        with st.status("Scanning sources...", expanded=True) as refresh_status:
            log_buffer = io.StringIO()
            with redirect_stdout(log_buffer):
                scan.run()
            st.code(log_buffer.getvalue(), language=None)
            refresh_status.update(label="Scoring new jobs...")
            summary = score_mod.score_unscored(conn, config.load_criteria())
            refresh_status.update(
                label=(
                    f"Done — scored {summary['scored']} new jobs"
                    f" (${summary['spent_usd']}), {summary['failed']} failed"
                ),
                state="complete",
            )
    except Exception as exc:
        st.error(f"Refresh failed: {exc}")


def _flags(row) -> list[str]:
    return json.loads(row["flags"] or "[]")


def _reasons(row) -> list[str]:
    return json.loads(row["fit_reasons"] or "[]")


def _age_days(row) -> int | None:
    return normalize.posting_age_days(row["posted_at"], now=_NOW)


def _job_header(row, state: str | None) -> None:
    state_badge = f" · `{state}`" if state and state != "discovered" else ""
    st.markdown(f"**{row['score']}/100 — {row['title']}** @ {row['company']}{state_badge}")
    bits = [row["location"] or row["location_bucket"]]
    age = _age_days(row)
    if age is not None:
        bits.append(f"posted {age}d ago")
    if row["repost_count"]:
        bits.append(f"reposted ×{row['repost_count']}")
    flag_text = " · ".join(_flags(row))
    if flag_text:
        bits.append(flag_text)
    st.caption(" · ".join(bits))


def _details(row) -> None:
    with st.expander("Details"):
        for reason in _reasons(row):
            st.markdown(f"- {reason}")
        if row["description"]:
            # Full description, scrollable — a hard [:1500] slice cut postings
            # off mid-sentence.
            with st.container(height=300, border=False):
                st.text(row["description"])
        urls = json.loads(row["source_urls"] or "[]")
        if len(urls) > 1:
            st.caption("Also seen at: " + " · ".join(u for u in urls if u != row["primary_url"]))


def _draft_section(row) -> None:
    draft = drafts.existing_draft(conn, row["id"])
    if draft:
        note, coaching = drafts.split_draft(draft)
        st.text_area(
            "Cover note (plain text — copy this)", note, height=260, key=f"draft-{row['id']}"
        )
        if coaching:
            with st.expander("For your eyes only — points to emphasize (NOT part of the note)"):
                st.markdown(coaching)
        label = "Regenerate draft (~$0.002)"
    else:
        label = "Draft cover note (~$0.002)"
    if st.button(label, key=f"draft-btn-{row['id']}"):
        with st.spinner("Drafting..."):
            drafts.generate(conn, row, config.load_resume())
        st.rerun()


def _notes_section(row) -> None:
    with st.expander("Notes"):
        for note in store.notes_for_job(conn, row["id"]):
            if note["body_md"].startswith(drafts.DRAFT_PREFIX):
                continue  # drafts have their own section
            st.markdown(note["body_md"])
            st.caption(note["created_at"])
            st.divider()
        new_note = st.text_area(
            "Add a note (contacts, comp discussed, interview prep...)",
            key=f"note-new-{row['id']}",
        )
        if st.button("Save note", key=f"note-save-{row['id']}") and new_note.strip():
            store.add_note(conn, row["id"], new_note.strip())
            st.rerun()


def _tracker_card(row) -> None:
    with st.container(border=True):
        col_main, col_apply, col_state = st.columns([5, 2, 3])
        with col_main:
            st.markdown(f"**{row['title']}** @ {row['company']} · `{row['state']}`")
            st.caption(row["location"] or row["location_bucket"])
        with col_apply:
            if row["primary_url"]:
                st.link_button("Apply ↗", row["primary_url"])
        with col_state:
            current = row["state"]
            new_state = st.selectbox(
                "Move to",
                tracker.STATES,
                index=tracker.STATES.index(current),
                key=f"state-{row['application_id']}",
                label_visibility="collapsed",
            )
            if new_state != current and st.button("Move", key=f"move-{row['application_id']}"):
                tracker.transition(conn, row["id"], new_state)
                st.rerun()
        _draft_section(row)
        _notes_section(row)


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
    # Ghost-job shield: >30-day-old postings are widely treated as likely
    # ghost. Default OFF — flag, don't hide silently.
    hide_stale = st.checkbox(f"Hide stale (>{STALE_DAYS} days)", value=False)

tab_inbox, tab_tracker, tab_funnel, tab_settings, tab_help = st.tabs(
    ["Inbox", "Tracker", "Funnel", "Settings", "Help"]
)

with tab_inbox:
    rows = store.scored_jobs(conn)
    filtered = [
        r
        for r in rows
        if r["score"] >= min_score
        and (not buckets or r["location_bucket"] in buckets)
        # Unknown age is NOT stale — only a known >30d age hides a job.
        and (not hide_stale or (_age_days(r) or 0) <= STALE_DAYS)
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

with tab_tracker:
    apps = tracker.applications_with_jobs(conn)
    if not apps:
        st.info("No applications yet — hit Shortlist on an Inbox job.")
    for row in apps:
        _tracker_card(row)

with tab_funnel:
    event_rows = tracker.events(conn)
    flows = sankey.build_flows([(e["from_state"], e["to_state"]) for e in event_rows])
    counts = tracker.pipeline_counts(conn)
    rate = tracker.response_rate(conn)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("In pipeline", sum(counts.values()))
    m2.metric("Applied", counts.get("applied", 0))
    m3.metric("Response rate", f"{rate:.0%}" if rate is not None else "—")
    m4.metric("Offers", counts.get("offer", 0))

    fig = sankey.figure(flows)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("The funnel draws itself once applications start moving through states.")

    stage_days = tracker.time_in_stage(event_rows)
    if stage_days:
        st.subheader("Average days in stage")
        for state in tracker.STATES:
            if state in stage_days:
                st.markdown(f"- **{state}**: {stage_days[state]:.1f} days")

with tab_settings:
    import os

    from ysearch import llm, paths, scan
    from ysearch.sources import jsearch

    data_root = paths.data_dir().resolve()
    st.caption(f"Data folder (config, db, keys): `{data_root}`")

    st.subheader("API keys")
    st.caption(
        f"Stored in `{data_root / '.env'}` (never committed) — your keys stay on your"
        " machine, are never shown in full, and never logged. Both have free tiers."
    )
    key_specs = [
        (
            "OPENWEBNINJA_API_KEY",
            "OpenWebNinja / JSearch key (job discovery)",
            "Free at openwebninja.com/api/jsearch — 200 requests/month, no card.",
            "Test key (uses 1 of your 200 monthly requests)",
            lambda: jsearch.ping(),
        ),
        (
            "OPENROUTER_API_KEY",
            "OpenRouter API key (scoring + drafts)",
            "Create at openrouter.ai/settings/keys — scoring costs ~$0.003/job.",
            "Test key (free — no tokens spent)",
            lambda: llm.check_key(),
        ),
    ]
    key_inputs: dict[str, str] = {}
    for env_name, label, hint, test_label, test_fn in key_specs:
        col_input, col_test = st.columns([3, 1], vertical_alignment="bottom")
        with col_input:
            key_inputs[env_name] = st.text_input(
                label, type="password", help=hint, key=f"key-{env_name}"
            )
            st.caption(f"Saved: `{llm.mask_key(os.environ.get(env_name))}`")
        with col_test:
            if st.button("Test key", key=f"test-{env_name}", help=test_label):
                with st.spinner("Testing..."):
                    ok, detail = test_fn()
                (st.success if ok else st.error)(detail)
    if st.button("Save keys"):
        config.save_env_values(key_inputs)
        st.success("Saved to .env — keys are active now.")
        st.rerun()

    st.subheader("Sources")
    st.caption("Which sources `ysearch scan` pulls from.")
    sources_now = scan.enabled_sources(conn)
    source_labels = {
        "jsearch": "JSearch (Google for Jobs — quota: 200 req/month free)",
        "ats": "ATS boards (Greenhouse / Lever / Ashby — free, no quota)",
        "naukri": "Naukri alert emails (set up in the section below)",
    }
    for source, label in source_labels.items():
        toggled = st.toggle(label, value=sources_now[source], key=f"src-{source}")
        if toggled != sources_now[source]:
            scan.set_source_enabled(conn, source, toggled)
            st.rerun()
    with st.expander("ATS watchlist (companies.yaml)"):
        st.caption(
            "Company slugs from careers-page URLs, e.g. boards.greenhouse.io/<slug>,"
            " jobs.lever.co/<slug>, jobs.ashbyhq.com/<slug>."
        )
        if "companies-editor" not in st.session_state:
            st.session_state["companies-editor"] = config.companies_yaml_text()
        st.text_area("companies.yaml", height=200, key="companies-editor")
        if st.button("Save watchlist"):
            try:
                config.save_companies_yaml(st.session_state["companies-editor"])
                st.success("Watchlist validated and saved.")
            except Exception as exc:
                st.error(f"Not saved — {exc}")

    st.subheader("Naukri / Email alerts")
    st.caption(
        "The real Naukri coverage: your naukri.com alerts land in Gmail under a label;"
        " ysearch reads ONLY that label over IMAP (read-only — nothing is sent, contents"
        " are never logged). Needs a Gmail app password (2FA required)."
    )
    from ysearch.sources import email_naukri

    imap_user, imap_password, imap_label = email_naukri.imap_settings()
    col_user, col_label = st.columns(2)
    with col_user:
        new_imap_user = st.text_input("Gmail address", value=imap_user or "", key="imap-user")
    with col_label:
        new_imap_label = st.text_input("IMAP label to watch", value=imap_label, key="imap-label")
    new_imap_password = st.text_input(
        "Gmail app password",
        type="password",
        key="imap-password",
        help="Create at myaccount.google.com/apppasswords — requires 2-factor auth.",
    )
    st.caption(f"Saved password: `{llm.mask_secret(imap_password)}`")
    col_email_save, col_email_test = st.columns([1, 2])
    with col_email_save:
        if st.button("Save email settings"):
            config.save_env_values(
                {
                    "YSEARCH_IMAP_USER": new_imap_user,
                    "YSEARCH_IMAP_PASSWORD": new_imap_password,
                    "YSEARCH_IMAP_LABEL": new_imap_label,
                }
            )
            st.success("Saved to .env — active now.")
            st.rerun()
    with col_email_test:
        if st.button("Test connection"):
            with st.spinner("Connecting to Gmail..."):
                ok, detail = email_naukri.test_connection()
            (st.success if ok else st.error)(detail)
    st.caption(
        "Once a real alert email lands under the label, run `ysearch scan` and check the"
        " parsed rows — the parser is fixture-built and may need mapping to the real format."
    )

    st.subheader("Resume")
    st.caption("Used to ground cover-note drafts — the AI may only claim what's in here.")
    try:
        existing_resume = config.load_resume()
    except FileNotFoundError:
        existing_resume = ""
    resume_text = st.text_area(
        "Paste your resume (plain text / markdown)", value=existing_resume, height=280
    )
    if st.button("Save resume") and resume_text.strip():
        config.save_resume(resume_text)
        st.success("Resume saved to config/resume.md (gitignored).")

    st.subheader("Criteria")
    st.caption(
        "What the scorer judges every posting against. Describe what you want and"
        " let the AI draft it — then review and save. Nothing saves without your click."
    )
    description = st.text_area(
        "Describe what you're looking for — roles, cities, remote, salary floor,"
        " your years of experience",
        key="criteria-description",
        placeholder="e.g. AI product manager or forward-deployed roles, remote or Chennai/"
        "Bangalore, 12 LPA minimum, I have 1.5 years of experience...",
    )
    if st.button("Generate criteria with AI (~$0.001)") and description.strip():
        from ysearch import onboard

        with st.spinner("Drafting criteria..."):
            st.session_state["criteria-editor"] = onboard.generate_criteria_yaml(
                description, resume_text or None
            )
        st.rerun()
    if "criteria-editor" not in st.session_state:
        st.session_state["criteria-editor"] = config.criteria_yaml_text()
    st.text_area("criteria.yaml (review and edit before saving)", height=320, key="criteria-editor")
    if st.button("Save criteria"):
        try:
            config.save_criteria_yaml(st.session_state["criteria-editor"])
            st.success("Criteria validated and saved.")
        except Exception as exc:
            st.error(f"Not saved — {exc}")

with tab_help:
    from ysearch import paths

    # Single source of truth: the Help tab IS the README — the clone's file in
    # repo mode, the copy packaged into the wheel in home mode.
    readme_md = paths.readme_text()
    if readme_md:
        st.markdown(readme_md)
    else:
        st.info("README not found — see github.com/AryanJ129/ysearch for the guide.")

conn.close()
