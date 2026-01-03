import json
import sqlite3
import logging
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Callable, TypeVar, ParamSpec

import pandas as pd

logger = logging.getLogger(__name__)

# Keep the database in the project root regardless of where the process is started from.
DB_PATH = str(Path(__file__).resolve().parents[2] / "autotrader.db")

# Type variables for the decorator
P = ParamSpec("P")
T = TypeVar("T")

# ----- PERSISTENT WRITE CONNECTION WITH BATCHING -----
# Reuse a single connection for all writes to avoid constant open/close overhead.
# Batch commits to reduce lock contention (commit every N seconds or N operations).
_write_conn_lock = threading.Lock()
_write_conn: sqlite3.Connection | None = None
_pending_writes = 0
_last_commit_time = 0.0
_BATCH_COMMIT_INTERVAL = 2.0  # Commit at most every 2 seconds
_BATCH_COMMIT_THRESHOLD = 50  # Or after 50 pending writes


def _get_write_conn() -> sqlite3.Connection:
    """
    Get the persistent write connection for the trader process.
    Creates the connection on first use. Thread-safe.
    """
    global _write_conn
    if _write_conn is None:
        with _write_conn_lock:
            if _write_conn is None:
                _write_conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, isolation_level="DEFERRED")
                _write_conn.execute("PRAGMA journal_mode=WAL")
                _write_conn.execute("PRAGMA synchronous=NORMAL")
                _write_conn.execute("PRAGMA busy_timeout=10000")  # Wait up to 10s for locks
                _write_conn.execute("PRAGMA wal_autocheckpoint=1000")  # Checkpoint every 1000 pages
                logger.info("Opened persistent write connection to database (batched commits)")
    return _write_conn


def _maybe_commit() -> None:
    """Commit if we've accumulated enough writes or enough time has passed."""
    global _pending_writes, _last_commit_time
    now = time.time()
    should_commit = (
        _pending_writes >= _BATCH_COMMIT_THRESHOLD or
        (now - _last_commit_time) >= _BATCH_COMMIT_INTERVAL
    )
    if should_commit and _write_conn is not None:
        try:
            _write_conn.commit()
            _pending_writes = 0
            _last_commit_time = now
        except Exception as e:
            logger.warning(f"Batch commit failed: {e}")


def _increment_pending() -> None:
    """Track a pending write and maybe commit."""
    global _pending_writes
    _pending_writes += 1
    _maybe_commit()


def force_commit() -> None:
    """Force an immediate commit (call at end of cycle or before long operations)."""
    global _pending_writes, _last_commit_time
    if _write_conn is not None:
        try:
            _write_conn.commit()
            _pending_writes = 0
            _last_commit_time = time.time()
        except Exception as e:
            logger.warning(f"Force commit failed: {e}")


def close_write_conn() -> None:
    """Close the persistent write connection (call on shutdown)."""
    global _write_conn
    if _write_conn is not None:
        with _write_conn_lock:
            if _write_conn is not None:
                _write_conn.close()
                _write_conn = None
                logger.info("Closed persistent write connection")


def safe_db_read(default_factory: Callable[[], T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator for database read functions that returns a default value on error.
    Prevents the API from crashing when the database is locked or unavailable.
    
    Usage:
        @safe_db_read(default_factory=pd.DataFrame)
        def get_something() -> pd.DataFrame:
            ...
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                logger.warning(f"Database read failed ({func.__name__}): {e}")
                return default_factory()
            except sqlite3.DatabaseError as e:
                logger.error(f"Database error ({func.__name__}): {e}")
                return default_factory()
            except Exception as e:
                logger.error(f"Unexpected error in {func.__name__}: {type(e).__name__}: {e}")
                return default_factory()
        return wrapper
    return decorator


def _connect_fresh() -> sqlite3.Connection:
    """
    Create a fresh connection (for init_db and one-time operations).
    The caller is responsible for closing this connection.
    """
    return sqlite3.connect(DB_PATH, timeout=30)


def _connect_ro() -> sqlite3.Connection:
    """
    Read connection for the API with optimised settings to minimise lock contention.
    Uses short timeout and immediate rollback to avoid holding locks.
    """
    conn = sqlite3.connect(DB_PATH, timeout=2, isolation_level=None)  # autocommit mode
    conn.execute("PRAGMA query_only = 1")  # Prevent accidental writes
    conn.execute("PRAGMA read_uncommitted = 1")  # Allow reading uncommitted data (faster)
    conn.execute("PRAGMA cache_size = 1000")  # Smaller cache for quick reads
    return conn


def init_db() -> None:
    """Initialise/upgrade the SQLite database schema (idempotent)."""
    conn = _connect_fresh()
    cursor = conn.cursor()

    # Better concurrency between the trader (writes) and dashboard (reads).
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            level TEXT,
            message TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            action TEXT,
            quantity INTEGER,
            price REAL,
            stop_loss REAL,
            take_profit REAL,
            sentiment_score REAL,
            status TEXT,
            rationale TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            equity REAL,
            unrealized_pnl REAL,
            realized_pnl REAL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS research_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            exchange TEXT,
            currency TEXT,
            price REAL,
            rsi REAL,
            volatility_ratio REAL,
            sentiment_score REAL,
            ai_reasoning TEXT,
            score REAL,
            rank INTEGER,
            reddit_mentions INTEGER,
            reddit_sentiment REAL,
            reddit_confidence REAL,
            reddit_override INTEGER,
            decision TEXT,
            reason TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS live_status (
            id INTEGER PRIMARY KEY,
            current_symbol TEXT,
            current_step TEXT,
            last_update DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS event_stream (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            level TEXT,
            symbol TEXT,
            step TEXT,
            message TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS account_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            tag TEXT,
            value REAL,
            currency TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS positions_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            account TEXT,
            symbol TEXT,
            exchange TEXT,
            currency TEXT,
            position REAL,
            avg_cost REAL,
            market_price REAL,
            market_value REAL,
            unrealised_pnl REAL,
            realised_pnl REAL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS open_orders_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            order_id INTEGER,
            symbol TEXT,
            exchange TEXT,
            currency TEXT,
            action TEXT,
            order_type TEXT,
            total_qty REAL,
            filled REAL,
            remaining REAL,
            status TEXT,
            lmt_price REAL,
            aux_price REAL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reddit_state (
            id INTEGER PRIMARY KEY,
            last_fetch_utc INTEGER,
            last_analysis_utc INTEGER
        )
        """
    )
    cursor.execute(
        """
        INSERT OR IGNORE INTO reddit_state (id, last_fetch_utc, last_analysis_utc)
        VALUES (1, 0, 0)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reddit_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reddit_id TEXT UNIQUE,
            subreddit TEXT,
            created_utc INTEGER,
            title TEXT,
            selftext TEXT,
            permalink TEXT,
            ups INTEGER,
            num_comments INTEGER
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reddit_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            mentions INTEGER,
            sentiment REAL,
            confidence REAL,
            rationale TEXT,
            source_fetch_utc INTEGER
        )
        """
    )

    # Runtime configuration overlay (editable from the dashboard).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_config (
            id INTEGER PRIMARY KEY,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            config_json TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        INSERT OR IGNORE INTO runtime_config (id, config_json)
        VALUES (
            1,
            '{"schema_version":1,"overrides":{},"strategies":[{"name":"Default","overrides":{}}],"active_strategy":"Default"}'
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS position_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT NOT NULL,
            exchange TEXT,
            currency TEXT,
            entry_price REAL,
            current_price REAL,
            quantity INTEGER,
            unrealised_pnl REAL,
            pnl_pct REAL,
            minutes_held INTEGER,
            current_stop_loss REAL,
            current_take_profit REAL,
            action TEXT NOT NULL,
            new_stop_loss REAL,
            new_take_profit REAL,
            confidence REAL,
            urgency REAL,
            rationale TEXT,
            key_factors TEXT,
            executed INTEGER DEFAULT 0
        )
        """
    )

    # Order reviews table - AI decisions on pending/stale orders
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS order_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            order_id INTEGER,
            symbol TEXT NOT NULL,
            order_type TEXT,  -- BUY, SELL, STP, LMT
            order_action TEXT,  -- BUY, SELL
            order_quantity INTEGER,
            order_price REAL,
            current_price REAL,
            bid_price REAL,
            ask_price REAL,
            price_distance_pct REAL,
            order_age_minutes INTEGER,
            action TEXT NOT NULL,  -- KEEP, CANCEL, ADJUST_PRICE
            new_price REAL,
            confidence REAL,
            rationale TEXT,
            executed INTEGER DEFAULT 0
        )
        """
    )

    # Singleton row for live status.
    cursor.execute(
        """
        INSERT OR IGNORE INTO live_status (id, current_symbol, current_step)
        VALUES (1, 'Idle', 'Waiting for cycle')
        """
    )

    # Best-effort schema upgrades for older databases.
    for ddl in [
        "ALTER TABLE trades ADD COLUMN take_profit REAL",
        "ALTER TABLE trades ADD COLUMN rationale TEXT",
        "ALTER TABLE research_log ADD COLUMN exchange TEXT",
        "ALTER TABLE research_log ADD COLUMN currency TEXT",
        "ALTER TABLE research_log ADD COLUMN score REAL",
        "ALTER TABLE research_log ADD COLUMN rank INTEGER",
        "ALTER TABLE research_log ADD COLUMN reddit_mentions INTEGER",
        "ALTER TABLE research_log ADD COLUMN reddit_sentiment REAL",
        "ALTER TABLE research_log ADD COLUMN reddit_confidence REAL",
        "ALTER TABLE research_log ADD COLUMN reddit_override INTEGER",
    ]:
        try:
            cursor.execute(ddl)
        except sqlite3.OperationalError:
            # Duplicate column, etc.
            pass

    conn.commit()
    conn.close()


def log_to_db(level: str, message: str) -> None:
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO logs (level, message) VALUES (?, ?)", (level, message))
    _increment_pending()  # Batched commit


def log_event(level: str, message: str, symbol: str | None = None, step: str | None = None) -> None:
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO event_stream (level, symbol, step, message) VALUES (?, ?, ?, ?)",
        (level, symbol, step, message),
    )
    _increment_pending()  # Batched commit


@safe_db_read(default_factory=pd.DataFrame)
def get_events(limit: int = 200) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM event_stream ORDER BY timestamp DESC, id DESC LIMIT ?",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()


def record_account_summary(tag: str, value: float, currency: str) -> None:
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO account_summary (tag, value, currency) VALUES (?, ?, ?)",
        (str(tag), float(value), str(currency)),
    )
    _increment_pending()  # Batched commit


@safe_db_read(default_factory=pd.DataFrame)
def get_latest_account_summary(limit: int = 200) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM account_summary ORDER BY timestamp DESC, id DESC LIMIT ?",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()


def snapshot_positions(rows: list[dict]) -> None:
    if not rows:
        return
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO positions_snapshot
        (account, symbol, exchange, currency, position, avg_cost, market_price, market_value, unrealised_pnl, realised_pnl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
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
        ],
    )
    conn.commit()


@safe_db_read(default_factory=pd.DataFrame)
def get_latest_positions() -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            """
            SELECT *
            FROM positions_snapshot
            WHERE timestamp = (SELECT MAX(timestamp) FROM positions_snapshot)
            ORDER BY market_value DESC
            """,
            conn,
        )
        return df
    finally:
        conn.close()


def snapshot_open_orders(rows: list[dict]) -> None:
    if not rows:
        return
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO open_orders_snapshot
        (order_id, symbol, exchange, currency, action, order_type, total_qty, filled, remaining, status, lmt_price, aux_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
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
        ],
    )
    conn.commit()


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


def get_runtime_config() -> dict:
    conn = _connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SELECT config_json FROM runtime_config WHERE id = 1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("runtime_config row (id=1) not found")
        raw = row[0]
        if raw is None:
            raise RuntimeError("runtime_config.config_json is NULL")
        doc = json.loads(str(raw))
        if not isinstance(doc, dict):
            raise RuntimeError(f"runtime_config.config_json must be a JSON object; got {type(doc).__name__}")
        return doc
    finally:
        conn.close()


def set_runtime_config(doc: dict) -> None:
    conn = _get_write_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO runtime_config (id, config_json, updated_at)
        VALUES (1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            config_json=excluded.config_json,
            updated_at=excluded.updated_at
        """,
        (json.dumps(doc, separators=(",", ":"), ensure_ascii=False),),
    )
    _maybe_commit()

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
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO trades (symbol, action, quantity, price, stop_loss, take_profit, sentiment_score, status, rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, action, quantity, price, stop_loss, take_profit, sentiment_score, status, rationale),
    )
    conn.commit()


@safe_db_read(default_factory=lambda: None)
def get_last_trade_for_symbol(symbol: str, action: str = "BUY"):
    """Get the latest trade for a symbol and action."""
    conn = _connect_ro()
    try:
        # Use a limit of 1 to get the most recent
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE symbol = ? AND action = ? ORDER BY timestamp DESC LIMIT 1",
            conn,
            params=(symbol, action),
        )
        if df.empty:
            return None
        return df.iloc[0].to_dict()
    finally:
        conn.close()

def update_performance(equity: float, unrealized_pnl: float, realized_pnl: float) -> None:
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO performance (equity, unrealized_pnl, realized_pnl)
        VALUES (?, ?, ?)
        """,
        (equity, unrealized_pnl, realized_pnl),
    )
    conn.commit()


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
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO research_log
        (symbol, exchange, currency, price, rsi, volatility_ratio, sentiment_score, ai_reasoning, score, rank,
         reddit_mentions, reddit_sentiment, reddit_confidence, reddit_override,
         decision, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    conn.commit()
    row_id = int(cursor.lastrowid)
    return row_id


def update_research_decision(row_id: int, decision: str, reason: str, rank: int | None = None) -> None:
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE research_log SET decision = ?, reason = ?, rank = ? WHERE id = ?",
        (decision, reason, rank, int(row_id)),
    )
    conn.commit()


def update_live_status(symbol: str, step: str) -> None:
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE live_status
        SET current_symbol = ?, current_step = ?, last_update = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (symbol, step),
    )
    _increment_pending()  # Batched commit


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


@safe_db_read(default_factory=pd.DataFrame)
def get_recent_logs(limit: int = 50) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?",
            conn,
            params=(int(limit),),
        )
        return df
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


@safe_db_read(default_factory=pd.DataFrame)
def get_research_logs(limit: int = 20) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM research_log ORDER BY timestamp DESC LIMIT ?",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()


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
    conn = _get_write_conn()
    cur = conn.cursor()
    if last_fetch_utc is not None:
        cur.execute("UPDATE reddit_state SET last_fetch_utc = ? WHERE id = 1", (int(last_fetch_utc),))
    if last_analysis_utc is not None:
        cur.execute("UPDATE reddit_state SET last_analysis_utc = ? WHERE id = 1", (int(last_analysis_utc),))
    conn.commit()


def insert_reddit_posts(rows: list[dict]) -> None:
    if not rows:
        return
    conn = _get_write_conn()
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT OR IGNORE INTO reddit_posts
        (reddit_id, subreddit, created_utc, title, selftext, permalink, ups, num_comments)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
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
        ],
    )
    conn.commit()


@safe_db_read(default_factory=pd.DataFrame)
def get_recent_reddit_posts(limit: int = 500) -> pd.DataFrame:
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM reddit_posts ORDER BY created_utc DESC, id DESC LIMIT ?",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()


def insert_reddit_sentiments(rows: list[dict]) -> None:
    if not rows:
        return
    conn = _get_write_conn()
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO reddit_sentiment
        (symbol, mentions, sentiment, confidence, rationale, source_fetch_utc)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.get("symbol"),
                int(r.get("mentions") or 0),
                float(r.get("sentiment")),
                float(r.get("confidence")),
                r.get("rationale"),
                int(r.get("source_fetch_utc") or 0),
            )
            for r in rows
        ],
    )
    conn.commit()


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
    """Get the latest Reddit sentiment row for a single symbol, or None if not available."""
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            """
            SELECT *
            FROM reddit_sentiment
            WHERE symbol = ?
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


# ----- Position Review Functions -----

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
    """Log an AI position review decision to the database."""
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO position_reviews
        (symbol, exchange, currency, entry_price, current_price, quantity,
         unrealised_pnl, pnl_pct, minutes_held, current_stop_loss, current_take_profit,
         action, new_stop_loss, new_take_profit, confidence, urgency, rationale, key_factors, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    conn.commit()
    return int(cursor.lastrowid)


def mark_position_review_executed(review_id: int) -> None:
    """Mark a position review as executed."""
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE position_reviews SET executed = 1 WHERE id = ?",
        (int(review_id),),
    )
    conn.commit()


@safe_db_read(default_factory=pd.DataFrame)
def get_position_reviews(limit: int = 100) -> pd.DataFrame:
    """Get recent position review decisions."""
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM position_reviews ORDER BY timestamp DESC, id DESC LIMIT ?",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()


@safe_db_read(default_factory=pd.DataFrame)
def get_position_reviews_for_symbol(symbol: str, limit: int = 20) -> pd.DataFrame:
    """Get position review history for a specific symbol."""
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM position_reviews WHERE symbol = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
            conn,
            params=(str(symbol), int(limit)),
        )
        return df
    finally:
        conn.close()


# ----- ORDER REVIEW FUNCTIONS -----

def log_order_review(
    order_id: int,
    symbol: str,
    order_type: str | None,
    order_action: str | None,
    order_quantity: int | None,
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
    """Log an AI order review decision to the database."""
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO order_reviews
        (order_id, symbol, order_type, order_action, order_quantity, order_price,
         current_price, bid_price, ask_price, price_distance_pct, order_age_minutes,
         action, new_price, confidence, rationale, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            symbol,
            order_type,
            order_action,
            order_quantity,
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
    conn.commit()
    return int(cursor.lastrowid)


def mark_order_review_executed(review_id: int) -> None:
    """Mark an order review as executed."""
    conn = _get_write_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE order_reviews SET executed = 1 WHERE id = ?",
        (int(review_id),),
    )
    conn.commit()


@safe_db_read(default_factory=pd.DataFrame)
def get_order_reviews(limit: int = 100) -> pd.DataFrame:
    """Get recent order review decisions."""
    conn = _connect_ro()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM order_reviews ORDER BY timestamp DESC, id DESC LIMIT ?",
            conn,
            params=(int(limit),),
        )
        return df
    finally:
        conn.close()

