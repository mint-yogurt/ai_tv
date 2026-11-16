"""The job queue: one table, claimed atomically, run one at a time by the worker."""

from __future__ import annotations

import sqlite3

from .db import dumps, now


class Blocked(Exception):
    """A job can't continue until something outside it changes (a tool installed, a file dropped in)."""


class Stopped(Blocked):
    """Someone pressed Stop: the job ends as cancelled, not as an error."""


ACTIVE = ("queued", "running")


def enqueue(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict | None = None,
    *,
    production_id: int | None = None,
    asset_id: int | None = None,
    priority: int = 0,
) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (kind, payload, production_id, asset_id, priority) VALUES (?, ?, ?, ?, ?)",
        (kind, dumps(payload or {}), production_id, asset_id, priority),
    )
    if kind == "production.run" and production_id is not None:
        # A new run of a production replaces its earlier stopped and blocked runs (failed ones keep
        # their traceback until Clear old errors).
        conn.execute("DELETE FROM jobs WHERE kind = 'production.run' AND production_id = ? AND id < ? "
                     "AND status IN ('blocked', 'cancelled')", (production_id, cur.lastrowid))
    return cur.lastrowid


def claim(conn: sqlite3.Connection) -> sqlite3.Row | None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY priority DESC, id LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, finished_at = NULL, "
            "attempts = attempts + 1, error = NULL WHERE id = ?",
            (now(), row["id"]),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()


def log(conn: sqlite3.Connection, job_id: int, line: str) -> None:
    conn.execute("UPDATE jobs SET log = log || ? WHERE id = ?", (f"[{now()}] {line}\n", job_id))


def finish(conn: sqlite3.Connection, job_id: int, result: dict | None = None) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', result = ?, finished_at = ? WHERE id = ?",
        (dumps(result or {}), now(), job_id),
    )


def fail(conn: sqlite3.Connection, job_id: int, error: str, status: str = "failed") -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
        (status, error, now(), job_id),
    )


def retry(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'queued', error = NULL, finished_at = NULL "
        "WHERE id = ? AND status IN ('failed', 'blocked', 'cancelled')",
        (job_id,),
    )


def cancel(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ? AND status IN ('queued', 'blocked')",
        (now(), job_id),
    )


def clear_dead(conn: sqlite3.Connection) -> int:
    """Delete finished jobs that only clutter the queue: failed and cancelled jobs, and blocked
    production runs (the production itself keeps its status and note). Jobs blocked on a missing tool
    stay, so they can be retried once it's installed."""
    return conn.execute("DELETE FROM jobs WHERE status IN ('failed', 'cancelled') "
                        "OR (status = 'blocked' AND kind = 'production.run')").rowcount


def requeue_stale(conn: sqlite3.Connection) -> int:
    """Jobs left 'running' by a worker that died are put back in the queue."""
    return conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'").rowcount
