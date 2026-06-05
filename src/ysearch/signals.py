"""Deterministic scam tells — regex over the posting text.

The model also judges scam_risk (rubric in prompts.SCORE_SYSTEM), but the
unambiguous fee-asking phrases are matched in CODE and force-unioned into the
flags after parse, so the marker can never be missed (score.py).

Patterns are kept HIGH-PRECISION on purpose: scam_risk caps the score at 20,
so a false positive buries a legit job. Softer tells (WhatsApp/Telegram-only
contact, too-good salary, unverifiable company) stay model-judged — they need
context a regex doesn't have.
"""

from __future__ import annotations

import re

_SCAM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # The classic India fee-scam asks — essentially never legit in a posting.
        r"\b(registration|joining|processing|verification)\s+(fees?|charges?)\b",
        r"\brefundable\s+(fees?|charges?|deposits?|amounts?)\b",
        r"\bsecurity\s+deposit\b",
        # "pay ₹15,000" / "pay Rs. 2,000" — the posting telling the CANDIDATE
        # to pay money. Requires a currency marker: bare "pay" phrasings
        # ("we pay for training", "never pay to apply") are legit.
        r"\bpay\s+(rs\.?|₹|inr)\s*[\d,]+",
        # Directed at the candidate ("send us…", "transfer me…"). Generic
        # "transfer money/payment" phrasing would false-positive on fintech
        # job descriptions, so us/me is required.
        r"\b(send|transfer)\s+(us|me)\s[^.\n]{0,30}\b(money|payments?|amounts?|fees?)\b",
    )
)


def has_scam_signals(text: str | None) -> bool:
    """True when the posting contains an unambiguous fee-asking phrase."""
    if not text:
        return False
    return any(p.search(text) for p in _SCAM_PATTERNS)
