from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def record_account_summary(tag: str, value: float, currency: str) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO account_summary (tag, value, currency) VALUES (%s, %s, %s)",
            (str(tag), float(value), str(currency)),
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_latest_account_summary(limit: int = 500) -> pd.DataFrame:
    """
    Get the latest account summary entries, ensuring the most recent for each tag/currency.
    """
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            f"""
            SELECT
                id,
                timestamp,
                tag,
                value,
                currency
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER(PARTITION BY tag, currency ORDER BY timestamp DESC, id DESC) as rn
                FROM account_summary
            ) x
            WHERE rn = 1
            ORDER BY timestamp DESC, id DESC
            LIMIT {int(limit)}
            """,
            conn,
        )
        return df
    finally:
        conn.close()


def snapshot_positions(rows: list[dict]) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_values  # type: ignore

    values = [
        (
            r.get("account"),
            r.get("symbol"),
            r.get("exchange"),
            r.get("currency"),
            r.get("position"),
            r.get("avg_cost"),
            r.get("market_price"),
            r.get("market_value"),
            r.get("unrealised_pnl"),
            r.get("realised_pnl"),
        )
        for r in rows
    ]
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO positions_snapshot
            (account, symbol, exchange, currency, position, avg_cost, market_price, market_value, unrealised_pnl, realised_pnl)
            VALUES %s
            """,
            values,
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_latest_positions() -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            """
            SELECT *
            FROM positions_snapshot
            WHERE timestamp = (SELECT MAX(timestamp) FROM positions_snapshot)
            ORDER BY id DESC
            """,
            conn,
        )
        return df
    finally:
        conn.close()


def snapshot_open_orders(rows: list[dict]) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_values  # type: ignore

    values = [
        (
            r.get("order_id"),
            r.get("symbol"),
            r.get("exchange"),
            r.get("currency"),
            r.get("action"),
            r.get("order_type"),
            r.get("total_qty"),
            r.get("filled"),
            r.get("remaining"),
            r.get("status"),
            r.get("lmt_price"),
            r.get("aux_price"),
        )
        for r in rows
    ]
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO open_orders_snapshot
            (order_id, symbol, exchange, currency, action, order_type, total_qty, filled, remaining, status, lmt_price, aux_price)
            VALUES %s
            """,
            values,
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_latest_open_orders() -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            """
            SELECT *
            FROM open_orders_snapshot
            WHERE timestamp = (SELECT MAX(timestamp) FROM open_orders_snapshot)
            ORDER BY id DESC
            """,
            conn,
        )
        return df
    finally:
        conn.close()




