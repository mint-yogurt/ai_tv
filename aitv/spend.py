"""Money (pipeline v2, part A): every paid call is a row in `spend`, summed into productions.spent_usd.

A production's budget_usd (0 = no limit) is checked before every stage and before every paid call, so
a run stops as blocked once it has spent its budget, instead of finding out at the end.
"""

from __future__ import annotations

import sqlite3

from .jobs import Blocked


def record(conn: sqlite3.Connection, production_id: int | None, stage: str | None, what: str, cost_usd: float | None,
           model: str | None = None, tokens_in: int | None = None, tokens_out: int | None = None,
           seconds: float | None = None, note: str = "") -> None:
    """One paid call. cost_usd None (a provider that doesn't report it) is kept as 0 with a note."""
    if cost_usd is None:
        note = (note + "; " if note else "") + "cost not reported"
    conn.execute(
        "INSERT INTO spend (production_id, stage, what, model, tokens_in, tokens_out, seconds, cost_usd, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (production_id, stage, what, model, tokens_in, tokens_out, seconds, float(cost_usd or 0.0), note))
    if production_id is not None:
        conn.execute("UPDATE productions SET spent_usd = (SELECT COALESCE(SUM(cost_usd), 0) FROM spend "
                     "WHERE production_id = ?) WHERE id = ?", (production_id, production_id))


def total(conn: sqlite3.Connection, production_id: int) -> float:
    return float(conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE production_id = ?",
                              (production_id,)).fetchone()[0])


def by_stage(conn: sqlite3.Connection, production_id: int) -> list[dict]:
    """[{stage, what, calls, cost_usd}] in the order the money was spent."""
    rows = conn.execute(
        "SELECT stage, what, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd, MIN(id) AS first FROM spend "
        "WHERE production_id = ? GROUP BY stage, what ORDER BY first", (production_id,)).fetchall()
    return [{"stage": r["stage"], "what": r["what"], "calls": r["calls"], "cost_usd": round(r["cost_usd"], 4)} for r in rows]


def check_budget(conn: sqlite3.Connection, production_id: int, doing: str = "continue") -> None:
    """Raise Blocked if the production has a budget and has spent it."""
    row = conn.execute("SELECT budget_usd FROM productions WHERE id = ?", (production_id,)).fetchone()
    budget = float(row["budget_usd"] or 0) if row else 0.0
    if budget > 0:
        spent = total(conn, production_id)
        if spent >= budget:
            raise Blocked(f"the budget is spent (${spent:.3f} of ${budget:.2f}); raise it to {doing}")
