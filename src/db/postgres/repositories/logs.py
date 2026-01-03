from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def log_to_db(level: str, message: str) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO logs (level, message) VALUES (%s, %s)", (level, message))


@safe_db_read(default_factory=pd.DataFrame)
def get_recent_logs(limit: int = 50) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM logs ORDER BY timestamp DESC LIMIT %s",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()




