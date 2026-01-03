from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def record_trade(
    symbol: str,
    action: str,
    quantity: int,
    price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    sentiment_score: float | None,
    status: str = "EXECUTED",
    rationale: str | None = None,
) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO trades (symbol, action, quantity, price, stop_loss, take_profit, sentiment_score, status, rationale)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                symbol,
                action,
                int(quantity),
                float(price) if price is not None else None,
                stop_loss,
                take_profit,
                sentiment_score,
                status,
                rationale,
            ),
        )


@safe_db_read(default_factory=lambda: None)
def get_last_trade_for_symbol(symbol: str, action: str = "BUY"):
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE symbol = %s AND action = %s ORDER BY timestamp DESC LIMIT 1",
            conn,
            params=(str(symbol).upper(), str(action).upper()),
        )
        if df.empty:
            return None
        return df.iloc[0].to_dict()
    finally:
        conn.close()


@safe_db_read(default_factory=pd.DataFrame)
def get_trades() -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp DESC", conn)
        return df
    finally:
        conn.close()


