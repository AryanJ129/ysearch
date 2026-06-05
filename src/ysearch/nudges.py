"""Deterministic pipeline nudges — the anti-application-black-hole layer.

Two mechanisms, both $0 and code-only:

- **Follow-up NUDGES** (computed live, never stored): applications sitting in
  `applied` with no movement and no logged follow-up for FOLLOW_UP_DAYS, plus
  shortlisted jobs never applied to for SHORTLIST_NUDGE_DAYS. The cure is
  acting on them — log a follow-up (saved as a note, which resets the clock)
  or move the state.

- **Auto-ghost SUGGESTIONS** (rows in status_suggestions, source='stalled'):
  applied/screen/interview with no movement for GHOST_AFTER_DAYS → suggest
  `ghosted` through the same Apply/Dismiss panel as email suggestions. One
  suggestion per stall episode: a dismissal holds until the application
  moves again. Suggestions never move state; the owner clicks.

All timestamps in the db are sqlite datetime('now') — UTC, naive,
lexicographically ordered — so SQL string comparison is safe.
"""

from __future__ import annotations

import datetime
import sqlite3

from ysearch import store

FOLLOW_UP_DAYS = 14
SHORTLIST_NUDGE_DAYS = 7
GHOST_AFTER_DAYS = 30
FOLLOW_UP_NOTE_PREFIX = "Followed up"

# States where prolonged silence means ghosted (not shortlisted — that one is
# the owner's own stall, nudged separately, never auto-ghosted).
GHOSTABLE_STATES = ("applied", "screen", "interview")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _days_since(db_ts: str, now: datetime.datetime) -> float | None:
    """Days since a sqlite 'YYYY-MM-DD HH:MM:SS' timestamp. None on garbage."""
    try:
        then = datetime.datetime.fromisoformat(db_ts)
    except (TypeError, ValueError):
        return None
    return (now - then).total_seconds() / 86400


def _stalled_apps(conn: sqlite3.Connection, states: tuple[str, ...]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(states))
    return conn.execute(
        f"""SELECT a.id AS application_id, a.job_id, a.state, j.title, j.company,
                  (SELECT MAX(at) FROM state_events e
                    WHERE e.application_id = a.id) AS last_event_at
           FROM applications a JOIN jobs j ON j.id = a.job_id
           WHERE a.state IN ({placeholders})""",
        states,
    ).fetchall()


def _last_follow_up_at(conn: sqlite3.Connection, job_id: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(created_at) FROM notes WHERE job_id = ? AND body_md LIKE ?",
        (job_id, f"{FOLLOW_UP_NOTE_PREFIX}%"),
    ).fetchone()
    return row[0]


def pending_nudges(conn: sqlite3.Connection, *, now: datetime.datetime | None = None) -> list[dict]:
    """Live-computed nudge list for the Tracker tab. Nothing is written."""
    now = now or _now()
    nudges: list[dict] = []
    for app in _stalled_apps(conn, ("applied",)):
        # The clock restarts on EITHER a state move or a logged follow-up.
        marks = [app["last_event_at"], _last_follow_up_at(conn, app["job_id"])]
        latest = max(m for m in marks if m) if any(marks) else None
        days = _days_since(latest, now) if latest else None
        if days is not None and days >= FOLLOW_UP_DAYS:
            nudges.append(_nudge(app, days, "follow_up"))
    for app in _stalled_apps(conn, ("shortlisted",)):
        days = _days_since(app["last_event_at"], now) if app["last_event_at"] else None
        if days is not None and days >= SHORTLIST_NUDGE_DAYS:
            nudges.append(_nudge(app, days, "apply_or_drop"))
    return nudges


def _nudge(app: sqlite3.Row, days: float, kind: str) -> dict:
    return {
        "application_id": app["application_id"],
        "job_id": app["job_id"],
        "title": app["title"],
        "company": app["company"],
        "state": app["state"],
        "days": int(days),
        "kind": kind,
    }


def log_follow_up(conn: sqlite3.Connection, job_id: int) -> None:
    """Record that the owner followed up — a real note, and the nudge clock
    resets because pending_nudges reads it."""
    store.add_note(conn, job_id, f"{FOLLOW_UP_NOTE_PREFIX} — no reply yet, pinged them again.")


def generate_stall_suggestions(
    conn: sqlite3.Connection, *, now: datetime.datetime | None = None
) -> int:
    """Write a ghosted-suggestion for each application stalled past
    GHOST_AFTER_DAYS. Idempotent per stall episode: any existing 'stalled'
    suggestion newer than the application's last movement (pending OR
    dismissed) suppresses a new one — a dismissal means "stop asking" until
    the application actually moves again."""
    now = now or _now()
    created = 0
    for app in _stalled_apps(conn, GHOSTABLE_STATES):
        days = _days_since(app["last_event_at"], now) if app["last_event_at"] else None
        if days is None or days < GHOST_AFTER_DAYS:
            continue
        exists = conn.execute(
            """SELECT 1 FROM status_suggestions
               WHERE application_id = ? AND source = 'stalled' AND created_at >= ?""",
            (app["application_id"], app["last_event_at"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO status_suggestions
                 (application_id, suggested_state, kind, source, email_subject, confidence)
               VALUES (?, 'ghosted', 'no_response', 'stalled', ?, 1.0)""",
            (
                app["application_id"],
                f"in {app['state']} {int(days)}d with no movement",
            ),
        )
        created += 1
    conn.commit()
    return created
