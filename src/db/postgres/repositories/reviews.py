from __future__ import annotations

import json

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def log_position_review(
    symbol: str,
    exchange: str | None,
    currency: str | None,
    entry_price: float | None,
    current_price: float | None,
    quantity: int | None,
    unrealised_pnl: float | None,
    pnl_pct: float | None,
    minutes_held: int | None,
    current_stop_loss: float | None,
    current_take_profit: float | None,
    action: str,
    new_stop_loss: float | None,
    new_take_profit: float | None,
    confidence: float | None,
    urgency: float | None,
    rationale: str | None,
    key_factors: list | None,
    executed: bool = False,
) -> int:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO position_reviews
            (symbol, exchange, currency, entry_price, current_price, quantity,
             unrealised_pnl, pnl_pct, minutes_held, current_stop_loss, current_take_profit,
             action, new_stop_loss, new_take_profit, confidence, urgency, rationale, key_factors, executed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                symbol,
                exchange,
                currency,
                entry_price,
                current_price,
                quantity,
                unrealised_pnl,
                pnl_pct,
                minutes_held,
                current_stop_loss,
                current_take_profit,
                action,
                new_stop_loss,
                new_take_profit,
                confidence,
                urgency,
                rationale,
                json.dumps(key_factors) if key_factors else None,
                1 if executed else 0,
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def mark_position_review_executed(review_id: int) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE position_reviews SET executed = 1 WHERE id = %s", (int(review_id),))


@safe_db_read(default_factory=pd.DataFrame)
def get_position_reviews(limit: int = 100) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM position_reviews ORDER BY timestamp DESC, id DESC LIMIT %s",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()


@safe_db_read(default_factory=pd.DataFrame)
def get_position_reviews_for_symbol(symbol: str, limit: int = 20) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM position_reviews WHERE symbol = %s ORDER BY timestamp DESC, id DESC LIMIT %s",
            conn,
            params=(str(symbol), int(limit)),
        )
        return df
    finally:
        conn.close()


def log_order_review(
    order_id: int,
    symbol: str,
    order_type: str,
    order_action: str,
    order_quantity: int,
    order_price: float | None,
    current_price: float | None,
    bid_price: float | None,
    ask_price: float | None,
    price_distance_pct: float | None,
    order_age_minutes: int | None,
    action: str,
    new_price: float | None,
    confidence: float | None,
    rationale: str | None,
    executed: bool = False,
) -> int:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO order_reviews
            (order_id, symbol, order_type, order_action, order_quantity, order_price,
             current_price, bid_price, ask_price, price_distance_pct, order_age_minutes,
             action, new_price, confidence, rationale, executed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                int(order_id),
                symbol,
                order_type,
                order_action,
                int(order_quantity),
                order_price,
                current_price,
                bid_price,
                ask_price,
                price_distance_pct,
                order_age_minutes,
                action,
                new_price,
                confidence,
                rationale,
                1 if executed else 0,
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def mark_order_review_executed(review_id: int) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE order_reviews SET executed = 1 WHERE id = %s", (int(review_id),))


@safe_db_read(default_factory=pd.DataFrame)
def get_order_reviews(limit: int = 100) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM order_reviews ORDER BY timestamp DESC, id DESC LIMIT %s",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()




