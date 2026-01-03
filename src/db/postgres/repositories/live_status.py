from __future__ import annotations

import pandas as pd

from src.db.postgres.pool import _connect_ro, _pg_write_conn, safe_db_read


def update_live_status(symbol: str, step: str) -> None:
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE live_status
            SET current_symbol = %s, current_step = %s, last_update = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (symbol, step),
        )


@safe_db_read(default_factory=lambda: None)
def get_live_status():
    conn = _connect_ro()
    try:
        df = pd.read_sql_query("SELECT * FROM live_status WHERE id = 1", conn)
        if df.empty:
            return None
        return df.iloc[0]
    finally:
        conn.close()




