from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def log_event(level: str, message: str, symbol: str | None = None, step: str | None = None) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO event_stream (level, symbol, step, message) VALUES (%s, %s, %s, %s)",
            (level, symbol, step, message),
        )


@safe_db_read(default_factory=pd.DataFrame)
def get_events(limit: int = 200) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM event_stream ORDER BY timestamp DESC, id DESC LIMIT %s",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()




