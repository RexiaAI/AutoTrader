import sys
import argparse
import os
import sqlite3
from pathlib import Path
from typing import Iterable


# Ensure repo root is on sys.path so `import src...` works when running as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _require_postgres_url(cli_url: str | None) -> str:
    url = (cli_url or os.environ.get("AUTOTRADER_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "PostgreSQL URL not set. Provide --postgres or set AUTOTRADER_DATABASE_URL."
        )
    if not (url.startswith("postgres://") or url.startswith("postgresql://")):
        raise SystemExit("AUTOTRADER_DATABASE_URL must start with postgres:// or postgresql://")
    return url


def _ensure_sqlite_columns(conn: sqlite3.Connection, table: str, expected: list[str]) -> None:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]  # type: ignore[index]
    missing = [c for c in expected if c not in cols]
    if missing:
        raise SystemExit(
            f"SQLite table {table!r} is missing columns: {missing}. "
            "Run the latest trader once to upgrade the SQLite schema, then re-run migration."
        )


def _chunks(cur: sqlite3.Cursor, size: int) -> Iterable[list[tuple]]:
    while True:
        rows = cur.fetchmany(size)
        if not rows:
            break
        yield rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate autotrader.db (SQLite) into PostgreSQL.")
    parser.add_argument("--sqlite", default="autotrader.db", help="Path to SQLite file (default: autotrader.db)")
    parser.add_argument(
        "--postgres",
        default=None,
        help="PostgreSQL URL. If omitted, uses AUTOTRADER_DATABASE_URL.",
    )
    parser.add_argument("--batch", type=int, default=2000, help="Insert batch size (default: 2000)")
    args = parser.parse_args()

    pg_url = _require_postgres_url(args.postgres)

    # Ensure the Postgres backend initialises with the right URL.
    os.environ["AUTOTRADER_DATABASE_URL"] = pg_url

    # Create schema in Postgres
    from src.utils import database_postgres as pgdb  # noqa: WPS433 (runtime import)

    pgdb.init_db()

    import psycopg2  # type: ignore
    from psycopg2.extras import execute_values  # type: ignore

    sqlite_path = str(args.sqlite)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = None

    pg_conn = psycopg2.connect(pg_url)
    pg_conn.autocommit = False

    tables: dict[str, list[str]] = {
        "logs": ["id", "timestamp", "level", "message"],
        "trades": ["id", "timestamp", "symbol", "action", "quantity", "price", "stop_loss", "take_profit", "sentiment_score", "status"],
        "performance": ["id", "timestamp", "equity", "unrealized_pnl", "realized_pnl"],
        "research_log": [
            "id",
            "timestamp",
            "symbol",
            "exchange",
            "currency",
            "price",
            "rsi",
            "volatility_ratio",
            "sentiment_score",
            "ai_reasoning",
            "score",
            "rank",
            "reddit_mentions",
            "reddit_sentiment",
            "reddit_confidence",
            "reddit_override",
            "decision",
            "reason",
        ],
        "live_status": ["id", "current_symbol", "current_step", "last_update"],
        "event_stream": ["id", "timestamp", "level", "symbol", "step", "message"],
        "account_summary": ["id", "timestamp", "tag", "value", "currency"],
        "positions_snapshot": [
            "id",
            "timestamp",
            "account",
            "symbol",
            "exchange",
            "currency",
            "position",
            "avg_cost",
            "market_price",
            "market_value",
            "unrealised_pnl",
            "realised_pnl",
        ],
        "open_orders_snapshot": [
            "id",
            "timestamp",
            "order_id",
            "symbol",
            "exchange",
            "currency",
            "action",
            "order_type",
            "total_qty",
            "filled",
            "remaining",
            "status",
            "lmt_price",
            "aux_price",
        ],
        "reddit_state": ["id", "last_fetch_utc", "last_analysis_utc"],
        "reddit_posts": [
            "id",
            "fetched_at",
            "reddit_id",
            "subreddit",
            "created_utc",
            "title",
            "selftext",
            "permalink",
            "ups",
            "num_comments",
        ],
        "reddit_sentiment": ["id", "timestamp", "symbol", "mentions", "sentiment", "confidence", "rationale", "source_fetch_utc"],
        "position_reviews": [
            "id",
            "timestamp",
            "symbol",
            "exchange",
            "currency",
            "entry_price",
            "current_price",
            "quantity",
            "unrealised_pnl",
            "pnl_pct",
            "minutes_held",
            "current_stop_loss",
            "current_take_profit",
            "action",
            "new_stop_loss",
            "new_take_profit",
            "confidence",
            "urgency",
            "rationale",
            "key_factors",
            "executed",
        ],
        "order_reviews": [
            "id",
            "timestamp",
            "order_id",
            "symbol",
            "order_type",
            "order_action",
            "order_quantity",
            "order_price",
            "current_price",
            "bid_price",
            "ask_price",
            "price_distance_pct",
            "order_age_minutes",
            "action",
            "new_price",
            "confidence",
            "rationale",
            "executed",
        ],
    }

    try:
        pg_cur = pg_conn.cursor()

        for table, cols in tables.items():
            _ensure_sqlite_columns(sqlite_conn, table, cols)
            s_cur = sqlite_conn.cursor()
            s_cur.execute(f"SELECT {', '.join(cols)} FROM {table}")

            col_list = ", ".join(cols)

            if table in {"live_status", "reddit_state"}:
                insert_sql = (
                    f"INSERT INTO {table} ({col_list}) VALUES %s "
                    "ON CONFLICT (id) DO UPDATE SET "
                    + ", ".join([f"{c}=EXCLUDED.{c}" for c in cols if c != "id"])
                )
            else:
                insert_sql = f"INSERT INTO {table} ({col_list}) VALUES %s ON CONFLICT (id) DO NOTHING"

            total = 0
            for batch in _chunks(s_cur, int(args.batch)):
                execute_values(pg_cur, insert_sql, batch, page_size=int(args.batch))
                pg_conn.commit()
                total += len(batch)

            print(f"{table}: migrated {total} rows")

        # Fix sequences for BIGSERIAL/BIGINT identity columns.
        serial_tables = [
            "logs",
            "trades",
            "performance",
            "research_log",
            "event_stream",
            "account_summary",
            "positions_snapshot",
            "open_orders_snapshot",
            "reddit_posts",
            "reddit_sentiment",
            "position_reviews",
            "order_reviews",
        ]
        for table in serial_tables:
            pg_cur.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    true
                )
                """
            )
        pg_conn.commit()
        print("Sequences updated.")
    finally:
        try:
            sqlite_conn.close()
        except Exception:
            pass
        try:
            pg_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()


