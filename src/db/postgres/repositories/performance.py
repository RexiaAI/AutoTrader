from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def update_performance(equity: float, unrealized_pnl: float, realized_pnl: float) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO performance (equity, unrealized_pnl, realized_pnl)
            VALUES (%s, %s, %s)
            """,
            (float(equity), float(unrealized_pnl), float(realized_pnl)),
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_performance_history() -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query("SELECT * FROM performance ORDER BY timestamp ASC", conn)
        return df
    finally:
        conn.close()


@safe_db_read(default_factory=lambda: None)
def get_performance_summary() -> dict | None:
    """
    Return a small performance summary derived from IBKR net liquidation snapshots.

    This is used for "account P&L over time" style metrics without loading the full history.
    """
    conn = _connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SELECT timestamp, equity FROM performance ORDER BY timestamp ASC, id ASC LIMIT 1")
        first = cur.fetchone()
        cur.execute("SELECT timestamp, equity FROM performance ORDER BY timestamp DESC, id DESC LIMIT 1")
        last = cur.fetchone()

        if not first or not last:
            return None

        baseline_ts, baseline_equity = first[0], first[1]
        latest_ts, latest_equity = last[0], last[1]
        if baseline_equity is None or latest_equity is None:
            return None

        baseline_equity_f = float(baseline_equity)
        latest_equity_f = float(latest_equity)
        return {
            "baseline_timestamp": str(baseline_ts),
            "baseline_equity": baseline_equity_f,
            "latest_timestamp": str(latest_ts),
            "latest_equity": latest_equity_f,
            "delta_equity": latest_equity_f - baseline_equity_f,
            "delta_pct": ((latest_equity_f - baseline_equity_f) / baseline_equity_f) if baseline_equity_f else None,
        }
    finally:
        conn.close()



