"""Naukri email parser — fixture-based (synthetic-but-realistic .eml).

# TODO mirror of the parser's marker: once a real Naukri alert email exists,
# sanitize it into a second fixture and validate field mapping against it.
"""

import email
from pathlib import Path

from ysearch.sources import email_naukri

FIXTURE = Path(__file__).parent / "fixtures" / "naukri_alert_synthetic.eml"


def _fixture_message():
    return email.message_from_bytes(FIXTURE.read_bytes())


def test_sender_routing():
    msg = _fixture_message()
    assert email_naukri.is_naukri_sender(msg)
    other = email.message_from_string("From: LinkedIn <jobs@linkedin.com>\n\nhi")
    assert not email_naukri.is_naukri_sender(other)


def test_parse_fixture_alert():
    jobs = email_naukri.parse_message(_fixture_message())
    # 3 job-listing links: 2 full cards + 1 degenerate, the settings link excluded
    assert len(jobs) == 3
    first = jobs[0]
    assert first.source == "naukri_email"
    assert first.title == "AI Product Manager"
    assert first.company == "Acme AI Technologies"
    assert first.location == "Chennai"
    assert "naukri.com/job-listings" in first.url
    assert first.posted_at and first.posted_at.startswith("2026-06-04")
    assert "AI product roadmap" in first.description
    assert jobs[1].location == "Bengaluru"


def test_degenerate_card_gets_safe_defaults():
    jobs = email_naukri.parse_message(_fixture_message())
    untagged = jobs[2]
    assert untagged.title == "Untagged Listing"
    assert untagged.company == "(unknown)"
    assert untagged.location is None


def test_malformed_html_never_crashes():
    assert email_naukri.parse_alert_html("") == []
    assert email_naukri.parse_alert_html("<div><a href='nothing'>x</a>") == []
    assert email_naukri.parse_alert_html("not html at all \x00") == []


def test_no_creds_paths(monkeypatch):
    monkeypatch.delenv("YSEARCH_IMAP_USER", raising=False)
    monkeypatch.delenv("YSEARCH_IMAP_PASSWORD", raising=False)
    assert email_naukri.have_creds() is False
    ok, detail = email_naukri.test_connection()  # must not attempt network
    assert ok is False and "not set" in detail
    assert email_naukri.fetch() == []


def test_label_defaults_to_ysearch(monkeypatch):
    monkeypatch.delenv("YSEARCH_IMAP_LABEL", raising=False)
    assert email_naukri.imap_settings()[2] == "ysearch"
