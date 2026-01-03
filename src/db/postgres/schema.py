from __future__ import annotations

from src.db.postgres.pool import _require_database_url


def init_db() -> None:
    """Initialise/upgrade the PostgreSQL schema (idempotent)."""
    import psycopg2  # type: ignore

    dsn = _require_database_url()
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                message TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                action TEXT,
                quantity INTEGER,
                price DOUBLE PRECISION,
                stop_loss DOUBLE PRECISION,
                take_profit DOUBLE PRECISION,
                sentiment_score DOUBLE PRECISION,
                status TEXT,
                rationale TEXT
            )
            """
        )

        # Best-effort schema upgrades for existing databases.
        # Keep these fast and idempotent.
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS rationale TEXT")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS performance (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                equity DOUBLE PRECISION,
                unrealized_pnl DOUBLE PRECISION,
                realized_pnl DOUBLE PRECISION
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS research_log (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                exchange TEXT,
                currency TEXT,
                price DOUBLE PRECISION,
                rsi DOUBLE PRECISION,
                volatility_ratio DOUBLE PRECISION,
                sentiment_score DOUBLE PRECISION,
                ai_reasoning TEXT,
                score DOUBLE PRECISION,
                rank INTEGER,
                reddit_mentions INTEGER,
                reddit_sentiment DOUBLE PRECISION,
                reddit_confidence DOUBLE PRECISION,
                reddit_override INTEGER,
                decision TEXT,
                reason TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS live_status (
                id INTEGER PRIMARY KEY,
                current_symbol TEXT,
                current_step TEXT,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS event_stream (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                symbol TEXT,
                step TEXT,
                message TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS account_summary (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tag TEXT,
                value DOUBLE PRECISION,
                currency TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS positions_snapshot (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                account TEXT,
                symbol TEXT,
                exchange TEXT,
                currency TEXT,
                position DOUBLE PRECISION,
                avg_cost DOUBLE PRECISION,
                market_price DOUBLE PRECISION,
                market_value DOUBLE PRECISION,
                unrealised_pnl DOUBLE PRECISION,
                realised_pnl DOUBLE PRECISION
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS open_orders_snapshot (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                order_id BIGINT,
                symbol TEXT,
                exchange TEXT,
                currency TEXT,
                action TEXT,
                order_type TEXT,
                total_qty DOUBLE PRECISION,
                filled DOUBLE PRECISION,
                remaining DOUBLE PRECISION,
                status TEXT,
                lmt_price DOUBLE PRECISION,
                aux_price DOUBLE PRECISION
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reddit_state (
                id INTEGER PRIMARY KEY,
                last_fetch_utc BIGINT,
                last_analysis_utc BIGINT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reddit_posts (
                id BIGSERIAL PRIMARY KEY,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reddit_id TEXT UNIQUE,
                subreddit TEXT,
                created_utc BIGINT,
                title TEXT,
                selftext TEXT,
                permalink TEXT,
                ups INTEGER,
                num_comments INTEGER
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reddit_sentiment (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                mentions INTEGER,
                sentiment DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                rationale TEXT,
                source_fetch_utc BIGINT
            )
            """
        )

        # Runtime configuration overlay (editable from the dashboard).
        # Stored in DB so the API + trader can share a single source of truth without editing YAML.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_config (
                id INTEGER PRIMARY KEY,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                config_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            INSERT INTO runtime_config (id, config_json)
            VALUES (
                1,
                '{"schema_version":1,"overrides":{},"strategies":[{"name":"Default","overrides":{}}],"active_strategy":"Default"}'
            )
            ON CONFLICT (id) DO NOTHING
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS position_reviews (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT NOT NULL,
                exchange TEXT,
                currency TEXT,
                entry_price DOUBLE PRECISION,
                current_price DOUBLE PRECISION,
                quantity INTEGER,
                unrealised_pnl DOUBLE PRECISION,
                pnl_pct DOUBLE PRECISION,
                minutes_held INTEGER,
                current_stop_loss DOUBLE PRECISION,
                current_take_profit DOUBLE PRECISION,
                action TEXT NOT NULL,
                new_stop_loss DOUBLE PRECISION,
                new_take_profit DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                urgency DOUBLE PRECISION,
                rationale TEXT,
                key_factors TEXT,
                executed INTEGER DEFAULT 0
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS order_reviews (
                id BIGSERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                order_id BIGINT,
                symbol TEXT NOT NULL,
                order_type TEXT,
                order_action TEXT,
                order_quantity INTEGER,
                order_price DOUBLE PRECISION,
                current_price DOUBLE PRECISION,
                bid_price DOUBLE PRECISION,
                ask_price DOUBLE PRECISION,
                price_distance_pct DOUBLE PRECISION,
                order_age_minutes INTEGER,
                action TEXT NOT NULL,
                new_price DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                rationale TEXT,
                executed INTEGER DEFAULT 0
            )
            """
        )

        # Singleton rows
        cur.execute(
            """
            INSERT INTO reddit_state (id, last_fetch_utc, last_analysis_utc)
            VALUES (1, 0, 0)
            ON CONFLICT (id) DO NOTHING
            """
        )

        cur.execute(
            """
            INSERT INTO live_status (id, current_symbol, current_step)
            VALUES (1, 'Idle', 'Waiting for cycle')
            ON CONFLICT (id) DO NOTHING
            """
        )
    finally:
        conn.close()


