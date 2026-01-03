from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


@safe_db_read(default_factory=lambda: {"last_fetch_utc": 0, "last_analysis_utc": 0})
def get_reddit_state() -> dict:
    conn = _connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SELECT last_fetch_utc, last_analysis_utc FROM reddit_state WHERE id = 1")
        row = cur.fetchone()
        if not row:
            return {"last_fetch_utc": 0, "last_analysis_utc": 0}
        return {"last_fetch_utc": int(row[0] or 0), "last_analysis_utc": int(row[1] or 0)}
    finally:
        conn.close()


def set_reddit_state(last_fetch_utc: int | None = None, last_analysis_utc: int | None = None) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        if last_fetch_utc is not None:
            cur.execute("UPDATE reddit_state SET last_fetch_utc = %s WHERE id = 1", (int(last_fetch_utc),))
        if last_analysis_utc is not None:
            cur.execute("UPDATE reddit_state SET last_analysis_utc = %s WHERE id = 1", (int(last_analysis_utc),))


def insert_reddit_posts(rows: list[dict]) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_values  # type: ignore

    values = [
        (
            r.get("reddit_id"),
            r.get("subreddit"),
            int(r.get("created_utc") or 0),
            r.get("title"),
            r.get("selftext"),
            r.get("permalink"),
            int(r.get("ups") or 0),
            int(r.get("num_comments") or 0),
        )
        for r in rows
    ]
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO reddit_posts
            (reddit_id, subreddit, created_utc, title, selftext, permalink, ups, num_comments)
            VALUES %s
            ON CONFLICT (reddit_id) DO NOTHING
            """,
            values,
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_recent_reddit_posts(limit: int = 500) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM reddit_posts ORDER BY created_utc DESC, id DESC LIMIT %s",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()


def insert_reddit_sentiments(rows: list[dict]) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_values  # type: ignore

    values = [
        (
            (r.get("symbol") or "").upper(),
            int(r.get("mentions") or 0),
            float(r.get("sentiment")),
            float(r.get("confidence")),
            r.get("rationale"),
            int(r.get("source_fetch_utc") or 0),
        )
        for r in rows
    ]
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO reddit_sentiment
            (symbol, mentions, sentiment, confidence, rationale, source_fetch_utc)
            VALUES %s
            """,
            values,
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_latest_reddit_sentiment() -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            """
            SELECT rs.*
            FROM reddit_sentiment rs
            INNER JOIN (
                SELECT symbol, MAX(timestamp) AS max_ts
                FROM reddit_sentiment
                GROUP BY symbol
            ) latest
            ON rs.symbol = latest.symbol AND rs.timestamp = latest.max_ts
            """,
            conn,
        )
        return df
    finally:
        conn.close()


@safe_db_read(default_factory=lambda: None)
def get_latest_reddit_sentiment_for_symbol(symbol: str) -> dict | None:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            """
            SELECT *
            FROM reddit_sentiment
            WHERE symbol = %s
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            conn,
            params=(str(symbol).upper(),),
        )
        if df.empty:
            return None
        return df.iloc[0].to_dict()
    finally:
        conn.close()




