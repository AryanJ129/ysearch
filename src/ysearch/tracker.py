"""Application state machine — thin Phase-2 primitives.

Every transition is recorded in state_events; the Phase-3 Sankey funnel reads
those events, so transitions must ONLY happen through transition().
"""

from __future__ import annotations

import sqlite3

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
