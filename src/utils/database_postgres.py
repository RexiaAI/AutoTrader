"""
PostgreSQL database backend (compatibility wrapper).

The concrete implementation is split into:
- `src/db/postgres/pool.py` (connection pool + helpers)
- `src/db/postgres/schema.py` (schema initialisation)
- `src/db/postgres/repositories/*` (table-focused repository functions)

This file re-exports the original public surface used throughout the codebase.
"""

from __future__ import annotations

from src.db.postgres.pool import DB_PATH, DATABASE_URL, _connect_ro, safe_db_read  # noqa: F401
from src.db.postgres.schema import init_db  # noqa: F401
from src.db.postgres.repositories.events import get_events, log_event  # noqa: F401
from src.db.postgres.repositories.live_status import get_live_status, update_live_status  # noqa: F401
from src.db.postgres.repositories.logs import get_recent_logs, log_to_db  # noqa: F401
from src.db.postgres.repositories.performance import (  # noqa: F401
    get_performance_history,
    get_performance_summary,
    update_performance,
)
from src.db.postgres.repositories.reddit import (  # noqa: F401
    get_latest_reddit_sentiment,
    get_latest_reddit_sentiment_for_symbol,
    get_recent_reddit_posts,
    get_reddit_state,
    insert_reddit_posts,
    insert_reddit_sentiments,
    set_reddit_state,
)
from src.db.postgres.repositories.research import (  # noqa: F401
    get_research_logs,
    log_research,
    update_research_decision,
)
from src.db.postgres.repositories.reviews import (  # noqa: F401
    get_order_reviews,
    get_position_reviews,
    get_position_reviews_for_symbol,
    log_order_review,
    log_position_review,
    mark_order_review_executed,
    mark_position_review_executed,
)
from src.db.postgres.repositories.runtime_config import (  # noqa: F401
    get_runtime_config,
    set_runtime_config,
)
from src.db.postgres.repositories.snapshots import (  # noqa: F401
    get_latest_account_summary,
    get_latest_open_orders,
    get_latest_positions,
    record_account_summary,
    snapshot_open_orders,
    snapshot_positions,
)
from src.db.postgres.repositories.trades import (  # noqa: F401
    get_last_trade_for_symbol,
    get_trades,
    record_trade,
)


# No-op for Postgres mode (we commit per operation).
def force_commit() -> None:
    return


def close_write_conn() -> None:
    # Pool handles lifecycle; nothing to do here.
    return




