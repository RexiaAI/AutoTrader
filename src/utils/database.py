"""
Database backend selector.

We previously used SQLite (`autotrader.db`). To eliminate recurring lock contention between the
trader and API, we now support PostgreSQL. Selection is configuration-driven:

- If `AUTOTRADER_DATABASE_URL` (or `DATABASE_URL`) starts with `postgres://` or `postgresql://`,
  the PostgreSQL backend is used.
- Otherwise, we default to the existing SQLite backend.

This file re-exports a consistent function surface used across the codebase.
"""

from __future__ import annotations

import os


def _use_postgres() -> bool:
    url = (os.environ.get("AUTOTRADER_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    return url.startswith("postgres://") or url.startswith("postgresql://")


if _use_postgres():
    from .database_postgres import *  # noqa: F401,F403
    from .database_postgres import _connect_ro  # noqa: F401
else:
    from .database_sqlite import *  # noqa: F401,F403
    from .database_sqlite import _connect_ro  # noqa: F401


