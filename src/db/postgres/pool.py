from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

# Fail fast on DB connection issues to avoid hangs in the API and tests.
_DEFAULT_CONNECT_TIMEOUT_SECONDS = int(os.environ.get("AUTOTRADER_PG_CONNECT_TIMEOUT_SECONDS", "3"))


def _with_connect_timeout(dsn: str) -> str:
    """
    Ensure the DSN includes a small connect timeout so connection attempts never hang indefinitely.

    Supports both URL-style DSNs (postgresql://...) and keyword DSNs (host=... dbname=...).
    """
    dsn = (dsn or "").strip()
    if not dsn:
        return dsn

    # Keyword DSN: append if not present.
    if "://" not in dsn:
        if "connect_timeout" in dsn:
            return dsn
        return f"{dsn} connect_timeout={_DEFAULT_CONNECT_TIMEOUT_SECONDS}"

    # URL DSN: add query param if not already set.
    try:
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        u = urlparse(dsn)
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        if "connect_timeout" not in q:
            q["connect_timeout"] = str(_DEFAULT_CONNECT_TIMEOUT_SECONDS)
        new_u = u._replace(query=urlencode(q, doseq=True))
        return urlunparse(new_u)
    except Exception:
        # Best-effort: if parsing fails, leave DSN as-is rather than corrupting it.
        return dsn


# Postgres connection string is provided via env var (do not hardcode credentials).
# Example: postgresql://autotrader:password@127.0.0.1:5432/autotrader
DATABASE_URL = _with_connect_timeout(
    (os.environ.get("AUTOTRADER_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
)

# Keep name for API compatibility; it's no longer a file path in Postgres mode.
DB_PATH = DATABASE_URL

P = ParamSpec("P")
T = TypeVar("T")


def _require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError(
            "PostgreSQL selected but AUTOTRADER_DATABASE_URL (or DATABASE_URL) is not set."
        )
    return DATABASE_URL


_pool_lock = threading.Lock()
_pool = None


def _get_pool():
    """
    Lazily create a ThreadedConnectionPool.
    We keep a small pool because the API uses a thread pool for DB reads and the trader can be chatty.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import psycopg2  # type: ignore  # noqa: F401
                from psycopg2.pool import ThreadedConnectionPool  # type: ignore

                dsn = _require_database_url()
                minconn = int(os.environ.get("AUTOTRADER_PG_POOL_MIN", "1"))
                maxconn = int(os.environ.get("AUTOTRADER_PG_POOL_MAX", "25"))
                _pool = ThreadedConnectionPool(minconn=minconn, maxconn=maxconn, dsn=dsn)
                logger.info("Initialised PostgreSQL connection pool (min=%s, max=%s)", minconn, maxconn)
    return _pool


class _PooledConn:
    """DBAPI-compatible wrapper whose close() returns the connection to the pool."""

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def close(self):  # noqa: D401 - DBAPI name
        """Return the connection to the pool (do not close the underlying socket)."""
        if self._conn is None:
            return
        try:
            try:
                self._conn.rollback()
            except Exception:
                pass
            # Reset session flags so a future borrower doesn't inherit read-only/autocommit state.
            try:
                self._conn.set_session(readonly=False, autocommit=False)
            except Exception:
                pass
        finally:
            self._pool.putconn(self._conn)
            self._conn = None


def safe_db_read(default_factory: Callable[[], T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator to make DB reads robust in the API thread pool.
    NOTE: We keep this behaviour for compatibility with the current API design.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"DB read failed in {func.__name__}: {e}")
                return default_factory()

        return wrapper

    return decorator


@contextmanager
def _pg_write_conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        # Ensure clean session state
        try:
            conn.rollback()
        except Exception:
            pass
        # A pooled connection may have been used for read-only access previously; force writeable session.
        try:
            conn.set_session(readonly=False, autocommit=False)
        except Exception:
            pass
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


def _connect_ro():
    """
    Obtain a pooled read-only connection. Call close() to return it to the pool.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        try:
            conn.rollback()
        except Exception:
            pass
        # Read-only + autocommit to avoid long-lived transactions in the API
        conn.set_session(readonly=True, autocommit=True)
    except Exception:
        pool.putconn(conn)
        raise
    return _PooledConn(conn, pool)


