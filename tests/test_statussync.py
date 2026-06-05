"""Email status sync: classification parse, matching, the fixture pipeline,
and the Apply/Dismiss lifecycle.

Hermetic: messages are loaded from .eml fixtures and fed straight to
process_messages (no IMAP), the LLM is a canned fake keyed on fixture
content, and telemetry lands in the tmp home (conftest's YSEARCH_HOME).
"""

from __future__ import annotations

import email
from pathlib import Path

import pytest

from ysearch import llm, statussync, store, tracker
from ysearch.models import Job

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return email.message_from_bytes((FIXTURES / name).read_bytes())


def _all_fixtures():
    return [
        _load("status_rejection_greenhouse.eml"),
        _load("status_interview_invite.eml"),
        _load("status_offer_unmatched.eml"),
        _load("status_marketing.eml"),
    ]


def _fake_chat(system, user, **kw):
    """Deterministic stand-in for the classifier, keyed on the fixtures'
    From addresses (single-line — body phrases wrap across lines in .eml)."""
    if "no-reply@greenhouse.io" in user:
        return '{"kind": "rejection", "company_guess": "Acme Analytics", "confidence": 0.95}', 0.0
    if "talent@betalabs.example" in user:
        return '{"kind": "interview_invite", "company_guess": "Beta Labs", "confidence": 0.9}', 0.0
    if "hr@gammasystems.example" in user:
        return '{"kind": "offer", "company_guess": "Gamma Systems", "confidence": 0.9}', 0.0
    return '{"kind": "other", "company_guess": null, "confidence": 0.8}', 0.0


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # home mode → telemetry stays out of the repo
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    yield conn
    conn.close()


def _seed_application(conn, company, key, state="applied"):
    job_id, _ = store.upsert_job(
        conn, Job(source="jsearch", title="AI PM", company=company), bucket="b", key=key
    )
    tracker.transition(conn, job_id, state)
    return job_id


# --- classification parse ---


def test_parse_classification_ladder():
    ok = '{"kind": "rejection", "company_guess": "Acme", "confidence": 0.9}'
    assert statussync.parse_classification(ok) == ("rejection", "Acme", 0.9)
    fenced = f"Here you go:\n```json\n{ok}\n```"
    assert statussync.parse_classification(fenced)[0] == "rejection"
    assert statussync.parse_classification("total garbage") == ("other", None, 0.0)
    bad_kind = '{"kind": "promotion", "company_guess": null, "confidence": 2.5}'
    kind, company, conf = statussync.parse_classification(bad_kind)
    assert kind == "other" and company is None and conf == 1.0  # clamped


# --- matching ---


def test_match_by_company_in_email_text(conn):
    _seed_application(conn, "Acme Analytics", "k1")
    app = statussync.match_application(conn, "update from acme analytics recruiting", None)
    assert app is not None and app["company"] == "Acme Analytics"


def test_match_by_company_guess_echo(conn):
    _seed_application(conn, "Beta Labs", "k1")
    # Company never appears in the email text — the model's guess bridges it.
    app = statussync.match_application(conn, "interview availability next week", "Beta Labs")
    assert app is not None and app["company"] == "Beta Labs"


def test_no_match_for_terminal_states_or_unknown(conn):
    _seed_application(conn, "Acme Analytics", "k1", state="rejected")
    assert statussync.match_application(conn, "acme analytics says hello", None) is None
    assert statussync.match_application(conn, "totally unrelated", "Gamma") is None


def test_short_company_names_never_match(conn):
    _seed_application(conn, "AI", "k1")  # 2 chars — would match half the internet
    assert statussync.match_application(conn, "we love ai here", "AI") is None


# --- the fixture pipeline (the plan's done-check) ---


def test_fixture_pipeline(conn, monkeypatch):
    """4 emails → 2 matched suggestions + 1 unmatched suggestion + 1 'other'
    (no suggestion); reprocessing the same emails is free and writes nothing."""
    monkeypatch.setattr(llm, "chat", _fake_chat)
    _seed_application(conn, "Acme Analytics", "k-acme")
    _seed_application(conn, "Beta Labs", "k-beta")

    summary = statussync.process_messages(conn, _all_fixtures())
    assert summary == {"new_emails": 4, "suggestions": 3}

    pending = statussync.pending_suggestions(conn)
    assert len(pending) == 3
    by_kind = {s["kind"]: s for s in pending}
    assert by_kind["rejection"]["application_id"] is not None
    assert by_kind["rejection"]["suggested_state"] == "rejected"
    assert by_kind["rejection"]["company"] == "Acme Analytics"
    assert by_kind["interview_invite"]["suggested_state"] == "interview"
    assert by_kind["interview_invite"]["company"] == "Beta Labs"
    # The offer is from a company with no application — surfaced, not lost.
    assert by_kind["offer"]["application_id"] is None
    assert by_kind["offer"]["company_guess"] == "Gamma Systems"

    # Dedupe: the same 4 emails again → nothing new, nothing duplicated.
    again = statussync.process_messages(conn, _all_fixtures())
    assert again == {"new_emails": 0, "suggestions": 0}
    assert len(statussync.pending_suggestions(conn)) == 3


def test_llm_failure_leaves_email_unprocessed_for_retry(conn, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("budget cap / network down")

    monkeypatch.setattr(llm, "chat", boom)
    summary = statussync.process_messages(conn, [_load("status_rejection_greenhouse.eml")])
    assert summary == {"new_emails": 0, "suggestions": 0}
    assert conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0] == 0
    # Next scan, the call works → the email processes.
    monkeypatch.setattr(llm, "chat", _fake_chat)
    _seed_application(conn, "Acme Analytics", "k-acme")
    summary = statussync.process_messages(conn, [_load("status_rejection_greenhouse.eml")])
    assert summary == {"new_emails": 1, "suggestions": 1}


def test_malformed_email_never_crashes(conn, monkeypatch):
    monkeypatch.setattr(llm, "chat", _fake_chat)
    msg = email.message_from_bytes(b"\xff\xfe not really an email at all")
    summary = statussync.process_messages(conn, [msg])
    # Classified (as other), processed, zero suggestions, zero crash.
    assert summary["suggestions"] == 0
    assert conn.execute("SELECT COUNT(*) FROM status_suggestions").fetchone()[0] == 0


# --- suggestion lifecycle ---


def test_apply_suggestion_transitions_and_notes(conn, monkeypatch):
    monkeypatch.setattr(llm, "chat", _fake_chat)
    job_id = _seed_application(conn, "Acme Analytics", "k-acme")
    statussync.process_messages(conn, [_load("status_rejection_greenhouse.eml")])
    sugg = statussync.pending_suggestions(conn)[0]

    statussync.apply_suggestion(conn, sugg["id"])

    app = tracker.application_for_job(conn, job_id)
    assert app["state"] == "rejected"  # moved through tracker.transition
    events = [e["to_state"] for e in tracker.events(conn)]
    assert "rejected" in events  # Sankey integrity: the move is an event
    notes = [n["body_md"] for n in store.notes_for_job(conn, job_id)]
    assert any("Status email" in n and "rejected" in n for n in notes)
    assert statussync.pending_suggestions(conn) == []


def test_dismiss_suggestion_moves_nothing(conn, monkeypatch):
    monkeypatch.setattr(llm, "chat", _fake_chat)
    job_id = _seed_application(conn, "Acme Analytics", "k-acme")
    statussync.process_messages(conn, [_load("status_rejection_greenhouse.eml")])
    sugg = statussync.pending_suggestions(conn)[0]

    statussync.dismiss_suggestion(conn, sugg["id"])

    assert tracker.application_for_job(conn, job_id)["state"] == "applied"  # untouched
    assert statussync.pending_suggestions(conn) == []


def test_apply_on_unmatched_suggestion_is_a_safe_noop(conn, monkeypatch):
    monkeypatch.setattr(llm, "chat", _fake_chat)
    statussync.process_messages(conn, [_load("status_offer_unmatched.eml")])
    sugg = statussync.pending_suggestions(conn)[0]
    assert sugg["application_id"] is None
    statussync.apply_suggestion(conn, sugg["id"])  # nothing to move — must not raise
    assert statussync.pending_suggestions(conn)[0]["resolution"] == "pending"
