"""Scam red-flags: deterministic regex net, policy cap, prompt consistency."""

from __future__ import annotations

import pytest

from ysearch import prompts, score, signals

# --- the regex net: scams it must catch ---


@pytest.mark.parametrize(
    "text",
    [
        "Selected candidates must pay a registration fee of 2000.",
        "A small joining fee applies before onboarding.",
        "Processing charges: Rs 1500, payable on selection.",
        "Verification fee required for background check.",
        "Deposit of ₹15,000 — fully refundable charges after 3 months.",
        "Refundable deposit required to reserve your seat.",
        "A security deposit secures your laptop and ID card.",
        "You will need to pay Rs. 2,000 for the starter kit.",
        "Candidates pay ₹15,000 upfront.",
        "Please send us the money via UPI to confirm.",
        "Transfer us the joining amount to the account below.",
    ],
)
def test_scam_phrases_caught(text):
    assert signals.has_scam_signals(text) is True


# --- legit postings that mention money/deposits and must NOT trigger ---


@pytest.mark.parametrize(
    "text",
    [
        "Salary deposit dates are the 1st of each month.",  # the plan's canary
        "We pay for training and certification programs.",
        "Never pay anyone to apply — report suspicious requests.",
        "Build money transfer features for our payments platform.",  # fintech JD
        "Experience with payment gateways and transfer reconciliation.",
        "Competitive pay, INR 12-15 LPA depending on experience.",
        "Process vendor fees and charges in the billing system.",
        "",
    ],
)
def test_legit_postings_not_flagged(text):
    assert signals.has_scam_signals(text) is False


def test_none_description_safe():
    assert signals.has_scam_signals(None) is False


# --- policy cap ---


def test_scam_risk_caps_score_at_20():
    assert score.apply_policy(92, ["scam_risk"]) == 20
    assert score.apply_policy(10, ["scam_risk"]) == 10  # already below — untouched


def test_scam_cap_beats_seniority_cap():
    # A scam posting that also asks 10 years: 20, not 60 — scam wins.
    assert score.apply_policy(95, ["scam_risk", "seniority_mismatch"]) == 20


def test_existing_caps_unchanged():
    assert score.apply_policy(92, ["seniority_mismatch"]) == 60
    assert score.apply_policy(92, []) == 92
    assert score.apply_policy(None, ["scam_risk"]) is None


# --- the model path: flag allowed through parse + forced union ---


def test_scam_risk_is_an_allowed_flag():
    assert "scam_risk" in prompts.ALLOWED_FLAGS
    reply = '{"score": 80, "fit_reasons": [], "flags": ["scam_risk"]}'
    _, _, flags = score.parse_reply(reply)
    assert flags == ["scam_risk"]


def test_prompt_consistency():
    """The rubric, the JSON schema line, and ALLOWED_FLAGS must agree — a flag
    the model is told about but the parser drops would vanish silently."""
    assert "scam_risk" in prompts.SCORE_SYSTEM
    # Every flag named in the system prompt's JSON line is parser-allowed.
    for flag in prompts.ALLOWED_FLAGS:
        assert flag in prompts.SCORE_SYSTEM, f"{flag} missing from SCORE_SYSTEM"


def test_scam_signal_forced_into_stored_flags(tmp_path, monkeypatch):
    """End-to-end through score_unscored: the model MISSES the scam (returns
    clean flags), the regex net still stamps the stored row and the cap holds."""
    import json

    from ysearch import llm, store
    from ysearch.config import Criteria
    from ysearch.models import Job

    monkeypatch.chdir(tmp_path)  # home mode → telemetry stays out of the repo
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    store.upsert_job(
        conn,
        Job(
            source="jsearch",
            title="Data Entry Executive",
            company="Totally Real Ltd",
            description="Work from home! Candidates must pay a registration fee of Rs 2000.",
        ),
        bucket="b",
        key="k",
    )
    monkeypatch.setattr(
        llm, "chat", lambda *a, **k: ('{"score": 90, "fit_reasons": [], "flags": []}', 0.0)
    )
    summary = score.score_unscored(conn, Criteria(queries=["x"]))
    assert summary["scored"] == 1
    row = store.scored_jobs(conn)[0]
    assert "scam_risk" in json.loads(row["flags"])
    assert row["score"] == 20
