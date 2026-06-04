"""Application state machine + pipeline stats.

Every transition is recorded in state_events; the Sankey funnel and the stats
read those events, so transitions must ONLY happen through transition().
"""

from __future__ import annotations

import datetime
import itertools
import sqlite3
from collections import defaultdict

STATES = (
    "discovered",
    "shortlisted",
    "applied",
    "screen",
    "interview",
    "offer",
    "rejected",
    "ghosted",
    "withdrawn",
)


def application_for_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM applications WHERE job_id = ? ORDER BY id LIMIT 1", (job_id,)
    ).fetchone()


def get_or_create(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = application_for_job(conn, job_id)
    if row is not None:
        return row
    cur = conn.execute("INSERT INTO applications (job_id) VALUES (?)", (job_id,))
    conn.execute(
        "INSERT INTO state_events (application_id, from_state, to_state) VALUES (?, NULL, 'discovered')",
        (cur.lastrowid,),
    )
    conn.commit()
    return application_for_job(conn, job_id)


def transition(conn: sqlite3.Connection, job_id: int, to_state: str) -> sqlite3.Row:
    if to_state not in STATES:
        raise ValueError(f"Unknown state {to_state!r} — one of {STATES}")
    app = get_or_create(conn, job_id)
    if app["state"] == to_state:
        return app  # no-op, no duplicate event
    conn.execute(
        "INSERT INTO state_events (application_id, from_state, to_state) VALUES (?, ?, ?)",
        (app["id"], app["state"], to_state),
    )
    conn.execute("UPDATE applications SET state = ? WHERE id = ?", (to_state, app["id"]))
    conn.commit()
    return application_for_job(conn, job_id)


def events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All state events, ordered per application — input for Sankey + stats."""
    return conn.execute("SELECT * FROM state_events ORDER BY application_id, id").fetchall()


def pipeline_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Current application count per state."""
    return dict(conn.execute("SELECT state, COUNT(*) FROM applications GROUP BY state").fetchall())


def response_rate(conn: sqlite3.Connection) -> float | None:
    """Of applications that reached `applied`, the share that got ANY human
    response (screen/interview/offer/rejected). Ghosted is a non-response."""
    applied = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT application_id FROM state_events WHERE to_state = 'applied'"
        )
    }
    if not applied:
        return None
    responded = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT application_id FROM state_events"
            " WHERE to_state IN ('screen', 'interview', 'offer', 'rejected')"
        )
    }
    return len(applied & responded) / len(applied)


def time_in_stage(event_rows: list) -> dict[str, float]:
    """Average days spent in each state, from consecutive event timestamps
    per application. Open-ended (current) stages are not counted."""
    durations: dict[str, list[float]] = defaultdict(list)
    by_app: dict[int, list] = defaultdict(list)
    for ev in event_rows:
        by_app[ev["application_id"]].append(ev)
    for evs in by_app.values():
        for first, second in itertools.pairwise(evs):
            start = datetime.datetime.fromisoformat(first["at"])
            end = datetime.datetime.fromisoformat(second["at"])
            durations[first["to_state"]].append((end - start).total_seconds() / 86400)
    return {state: sum(days) / len(days) for state, days in durations.items()}


def applications_with_jobs(
    conn: sqlite3.Connection, states: tuple[str, ...] | None = None
) -> list[sqlite3.Row]:
    sql = """SELECT a.id AS application_id, a.state, a.created_at AS app_created_at, j.*
             FROM applications a JOIN jobs j ON j.id = a.job_id"""
    params: tuple = ()
    if states:
        sql += f" WHERE a.state IN ({','.join('?' * len(states))})"
        params = states
    sql += " ORDER BY a.created_at DESC"
    return conn.execute(sql, params).fetchall()
