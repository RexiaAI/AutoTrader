from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def log_research(
    symbol: str,
    exchange: str | None,
    currency: str | None,
    price: float,
    rsi: float,
    vol_ratio: float,
    sentiment: float,
    reasoning: str,
    score: float | None,
    rank: int | None,
    reddit_mentions: int | None,
    reddit_sentiment: float | None,
    reddit_confidence: float | None,
    reddit_override: int | None,
    decision: str,
    reason: str,
) -> int:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO research_log
            (symbol, exchange, currency, price, rsi, volatility_ratio, sentiment_score, ai_reasoning, score, rank,
             reddit_mentions, reddit_sentiment, reddit_confidence, reddit_override,
             decision, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                symbol,
                exchange,
                currency,
                price,
                rsi,
                vol_ratio,
                sentiment,
                reasoning,
                score,
                rank,
                reddit_mentions,
                reddit_sentiment,
                reddit_confidence,
                reddit_override,
                decision,
                reason,
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def update_research_decision(row_id: int, decision: str, reason: str, rank: int | None = None) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE research_log SET decision = %s, reason = %s, rank = %s WHERE id = %s",
            (decision, reason, rank, int(row_id)),
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_research_logs(limit: int = 20) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM research_log ORDER BY timestamp DESC LIMIT %s",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()




