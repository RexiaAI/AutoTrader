from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from src.utils.config_loader import load_config

if TYPE_CHECKING:
    from src.api.ibkr_service import IBKRService

logger = logging.getLogger(__name__)

_ibkr_service: IBKRService | None = None

# Thread pool for blocking DB operations so they don't freeze the event loop.
# Increased workers to handle concurrent requests without queuing.
_db_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="db_ro")


def _require_ibkr_service() -> IBKRService:
    if _ibkr_service is None or not _ibkr_service.is_ready():
        raise HTTPException(status_code=503, detail="IBKR service not ready")
    if not _ibkr_service.is_connected():
        raise HTTPException(status_code=503, detail="IBKR not connected")
    return _ibkr_service

from src.utils.database import (
    DB_PATH,
    _connect_ro,
    get_events,
    get_runtime_config,
    get_latest_open_orders,
    get_latest_reddit_sentiment,
    get_live_status,
    get_order_reviews,
    get_performance_history,
    get_performance_summary,
    get_position_reviews,
    get_recent_reddit_posts,
    get_reddit_state,
    get_research_logs,
    get_trades,
    set_runtime_config,
)

from src.utils.runtime_config import apply_runtime_config, normalise_runtime_config, validate_runtime_config
from src.research.prompts import get_prompt_templates


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return jsonable_encoder(df.to_dict(orient="records"))


app = FastAPI(
    title="AutoTrader API",
    version="0.1.0",
)

@app.on_event("startup")
async def startup_event():
    global _ibkr_service
    if str(os.environ.get("AUTOTRADER_DISABLE_IBKR_SERVICE", "")).strip() in {"1", "true", "TRUE", "yes", "YES"}:
        logger.info("IBKRService startup skipped (AUTOTRADER_DISABLE_IBKR_SERVICE set).")
        _ibkr_service = None
        return

    # Import lazily so unit tests can run without IBKR dependencies installed.
    from src.api.ibkr_service import IBKRService

    cfg = load_config()
    broker_cfg = cfg.get("broker", {}) if isinstance(cfg, dict) else {}
    host = str(broker_cfg.get("host", "127.0.0.1"))
    port = int(broker_cfg.get("port", 7497))

    # Use a different client ID than the trader to avoid collisions.
    # Single-instance design: API is always `trader_client_id + 1000`.
    trader_client_id = int(broker_cfg.get("client_id", 10))
    client_id = trader_client_id + 1000

    _ibkr_service = IBKRService(
        host=host,
        port=port,
        client_id=client_id,
        connect_timeout=10.0,
        request_timeout=8.0,
        reconnect_cooldown_seconds=10.0,
    )
    _ibkr_service.start()
    logger.info(f"IBKRService started (host={host}, port={port}, clientId={client_id})")

@app.on_event("shutdown")
async def shutdown_event():
    global _ibkr_service
    if _ibkr_service:
        _ibkr_service.stop()
        _ibkr_service = None
        logger.info("IBKRService stopped")

# Local dev defaults.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# Note: Request timeout middleware removed - it conflicts with Starlette's middleware pattern.
# Timeouts are now handled at the individual endpoint level via run_in_executor with timeout.


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler to catch unhandled errors and return a clean JSON response.
    Prevents the API from crashing or hanging on unexpected errors.
    """
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}")
    logger.debug(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Internal server error: {type(exc).__name__}",
            "message": str(exc)[:200],  # Truncate long error messages
        },
    )


async def _run_in_executor(func, *args, timeout_seconds: float = 3.0, **kwargs):
    """
    Run a blocking function in the thread pool executor with a timeout.
    If the function takes longer than timeout_seconds, returns None.
    Short timeout (3s) ensures the API stays responsive even under DB contention.
    """
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_db_executor, lambda: func(*args, **kwargs)),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Database call timed out after {timeout_seconds}s: {func.__name__}")
        return None
    except Exception as e:
        logger.warning(f"Database call failed: {func.__name__}: {e}")
        return None


async def _run_in_executor_strict(func, *args, timeout_seconds: float = 3.0, **kwargs):
    """
    Run a blocking function in the DB executor, but fail loudly (no silent fallbacks).
    Used for runtime config endpoints where partial/empty responses are dangerous.
    """
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_db_executor, lambda: func(*args, **kwargs)),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=503, detail=f"Database call timed out: {func.__name__}") from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database call failed: {func.__name__}: {type(e).__name__}: {str(e)[:200]}",
        ) from e


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """
    Health check that tests database connectivity.
    Uses a direct connection rather than the thread pool to avoid contention.
    """
    db_ok = False
    db_error = None
    try:
        # Direct test - don't use thread pool which may be saturated.
        # Works for both SQLite and PostgreSQL backends.
        conn = _connect_ro()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        db_ok = True
    except Exception as e:
        db_error = str(e)

    # Do not leak credentials if using PostgreSQL
    db_path_safe = DB_PATH
    try:
        if isinstance(DB_PATH, str) and (DB_PATH.startswith("postgres://") or DB_PATH.startswith("postgresql://")):
            from urllib.parse import urlparse

            u = urlparse(DB_PATH)
            host = u.hostname or "localhost"
            port = u.port or 5432
            dbname = (u.path or "/").lstrip("/") or "postgres"
            db_path_safe = f"{u.scheme}://{host}:{port}/{dbname}"
    except Exception:
        db_path_safe = "postgresql://<redacted>"
    
    return {
        "status": "ok" if db_ok else "degraded",
        "db_path": db_path_safe,
        "db_ok": db_ok,
        "db_error": db_error,
    }


def _test_db_connection() -> bool:
    """Quick DB connectivity test."""
    conn = _connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/live-status")
async def live_status() -> dict[str, Any]:
    row = await _run_in_executor(get_live_status)
    if row is None:
        return {"current_symbol": None, "current_step": None, "last_update": None}
    return jsonable_encoder(row.to_dict())


@app.get("/api/config/runtime")
async def config_runtime() -> dict[str, Any]:
    """
    Runtime configuration editable from the dashboard.

    Stored in DB so the API + trader share a single source of truth.
    """
    doc = await _run_in_executor_strict(get_runtime_config, timeout_seconds=3.0)
    try:
        doc_n = normalise_runtime_config(doc)
        validate_runtime_config(doc_n)
        return jsonable_encoder(doc_n)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Runtime config is invalid: {type(e).__name__}: {str(e)[:200]}",
        ) from e


@app.put("/api/config/runtime")
async def config_runtime_put(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Replace the runtime config document (strict validation; no partial fallbacks).
    """
    try:
        doc_n = normalise_runtime_config(payload)
        validate_runtime_config(doc_n)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid runtime config: {type(e).__name__}: {str(e)[:200]}",
        ) from e

    await _run_in_executor_strict(set_runtime_config, doc_n, timeout_seconds=3.0)
    return jsonable_encoder(doc_n)


@app.get("/api/config/effective")
async def config_effective() -> dict[str, Any]:
    """
    Effective config used by the trader: base YAML + runtime overrides + active strategy.
    """
    base = load_config()
    runtime = await _run_in_executor_strict(get_runtime_config, timeout_seconds=3.0)
    runtime_n = normalise_runtime_config(runtime)
    try:
        validate_runtime_config(runtime_n)
        effective = apply_runtime_config(base, runtime_n)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to apply runtime config: {type(e).__name__}: {str(e)[:200]}",
        ) from e
    return jsonable_encoder(
        {
            "runtime": runtime_n,
            "effective": effective,
        }
    )


@app.get("/api/config/prompt-templates")
async def config_prompt_templates() -> dict[str, Any]:
    """
    Built-in prompt templates used by the AI modules.

    These are the *strategy instructions* only (no OUTPUT schema), because the output format
    is enforced by the code and should not be something users have to manage.
    """
    return jsonable_encoder(get_prompt_templates())


@app.get("/api/events")
async def events(limit: int = Query(default=200, ge=1, le=2000)) -> list[dict[str, Any]]:
    df = await _run_in_executor(get_events, limit=limit)
    return _df_to_records(df)

@app.get("/api/history/events")
async def history_events(limit: int = Query(default=200, ge=1, le=2000)) -> list[dict[str, Any]]:
    """DB-backed events (history source of truth)."""
    return await events(limit=limit)


@app.get("/api/research")
async def research(limit: int = Query(default=200, ge=1, le=2000)) -> list[dict[str, Any]]:
    df = await _run_in_executor(get_research_logs, limit=limit)
    return _df_to_records(df)

@app.get("/api/history/research")
async def history_research(limit: int = Query(default=200, ge=1, le=2000)) -> list[dict[str, Any]]:
    """DB-backed research log (history source of truth)."""
    return await research(limit=limit)


@app.get("/api/history/open-orders")
async def history_open_orders(limit: int = Query(default=2000, ge=1, le=5000)) -> list[dict[str, Any]]:
    """DB-backed open orders snapshot (trader view)."""
    df = await _run_in_executor(get_latest_open_orders)
    if df is None or df.empty:
        return []
    return _df_to_records(df.head(int(limit)))


@app.get("/api/trades")
async def trades(limit: int = Query(default=500, ge=1, le=5000)) -> list[dict[str, Any]]:
    df = await _run_in_executor(get_trades)
    if df is None or df.empty:
        return []
    return _df_to_records(df.head(int(limit)))

@app.get("/api/history/trades")
async def history_trades(limit: int = Query(default=500, ge=1, le=5000)) -> list[dict[str, Any]]:
    """DB-backed trades (history source of truth)."""
    return await trades(limit=limit)


@app.get("/api/performance")
async def performance() -> list[dict[str, Any]]:
    df = await _run_in_executor(get_performance_history)
    return _df_to_records(df)

@app.get("/api/history/performance")
async def history_performance() -> list[dict[str, Any]]:
    """DB-backed performance (history source of truth)."""
    return await performance()


@app.get("/api/history/performance/summary")
async def history_performance_summary() -> dict[str, Any]:
    """
    DB-backed performance summary (net liquidation delta over time).
    """
    summary = await _run_in_executor(get_performance_summary)
    return jsonable_encoder(summary or {})


@app.get("/api/account-summary/latest")
async def account_summary_latest() -> list[dict[str, Any]]:
    """Fetch live account summary from IBKR."""
    svc = _require_ibkr_service()
    try:
        return [r.to_dict() for r in await svc.get_account_summary()]
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"IBKR account summary unavailable: {type(e).__name__}: {str(e)[:200]}",
        )

@app.get("/api/live/account-summary")
async def live_account_summary() -> list[dict[str, Any]]:
    """Live account summary (IBKR source of truth)."""
    return await account_summary_latest()


@app.get("/api/positions/latest")
async def positions_latest() -> list[dict[str, Any]]:
    """Fetch live positions directly from IBKR."""
    svc = _require_ibkr_service()
    try:
        return [r.to_dict() for r in await svc.get_positions()]
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"IBKR positions unavailable: {type(e).__name__}: {str(e)[:200]}",
        )

@app.get("/api/live/positions")
async def live_positions() -> list[dict[str, Any]]:
    """Live positions (IBKR source of truth)."""
    return await positions_latest()


@app.get("/api/open-orders/latest")
async def open_orders_latest() -> list[dict[str, Any]]:
    """Fetch live open orders directly from IBKR."""
    svc = _require_ibkr_service()
    try:
        return [r.to_dict() for r in await svc.get_open_orders()]
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"IBKR open orders unavailable: {type(e).__name__}: {str(e)[:200]}",
        )

@app.get("/api/live/open-orders")
async def live_open_orders() -> list[dict[str, Any]]:
    """Live open orders (IBKR source of truth)."""
    return await open_orders_latest()


@app.get("/api/reddit/state")
async def reddit_state() -> dict[str, Any]:
    state = await _run_in_executor(get_reddit_state)
    if state is None:
        return {"last_fetch_utc": 0, "last_analysis_utc": 0}
    return jsonable_encoder(state)


@app.get("/api/reddit/posts")
async def reddit_posts(limit: int = Query(default=300, ge=1, le=5000)) -> list[dict[str, Any]]:
    df = await _run_in_executor(get_recent_reddit_posts, limit=limit)
    return _df_to_records(df)


@app.get("/api/reddit/sentiment/latest")
async def reddit_sentiment_latest() -> list[dict[str, Any]]:
    df = await _run_in_executor(get_latest_reddit_sentiment)
    return _df_to_records(df)


@app.get("/api/position-reviews")
async def position_reviews(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    """Get recent AI position review decisions."""
    df = await _run_in_executor(get_position_reviews, limit=limit)
    return _df_to_records(df)

@app.get("/api/history/position-reviews")
async def history_position_reviews(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    """DB-backed position reviews (history source of truth)."""
    return await position_reviews(limit=limit)


@app.get("/api/order-reviews")
async def order_reviews(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    """Get recent AI order review decisions."""
    df = await _run_in_executor(get_order_reviews, limit=limit)
    return _df_to_records(df)

@app.get("/api/history/order-reviews")
async def history_order_reviews(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    """DB-backed order reviews (history source of truth)."""
    return await order_reviews(limit=limit)


def _fetch_events_after(last_id: int) -> list[tuple]:
    """Blocking DB read - runs in thread pool. Returns empty list on any error."""
    conn = None
    try:
        conn = _connect_ro()
        cur = conn.cursor()
        # Use %s for PostgreSQL, ? for SQLite
        placeholder = "%s" if (isinstance(DB_PATH, str) and "postgres" in DB_PATH.lower()) else "?"
        cur.execute(
            f"""
            SELECT id, timestamp, level, symbol, step, message
            FROM event_stream
            WHERE id > {placeholder}
            ORDER BY id ASC
            LIMIT 500
            """,
            (last_id,),
        )
        return cur.fetchall()
    except Exception as e:
        logger.warning(f"_fetch_events_after failed: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@app.get("/api/events/stream")
async def events_stream(
    request: Request,
    after_id: int = Query(default=0, ge=0),
    poll_seconds: float = Query(default=1.0, ge=0.2, le=10.0),
):
    """
    Server-Sent Events feed of the event stream table.
    Uses asyncio.sleep and runs DB in thread pool to avoid blocking the event loop.
    """

    async def _gen():
        last_id = int(after_id)
        loop = asyncio.get_running_loop()
        # Hint to clients how long to wait before reconnecting (milliseconds).
        yield "retry: 1000\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break

                # Run blocking DB call in thread pool with timeout to prevent hangs.
                try:
                    rows = await asyncio.wait_for(
                        loop.run_in_executor(_db_executor, _fetch_events_after, last_id),
                        timeout=3.0  # Short timeout to keep SSE responsive
                    )
                except asyncio.TimeoutError:
                    rows = None  # Skip this poll if DB is slow
                except Exception:
                    rows = None  # Skip on any DB error

                if rows:
                    for r in rows:
                        last_id = int(r[0])
                        ts = r[1]
                        try:
                            # PostgreSQL returns datetime objects; JSON needs strings.
                            if hasattr(ts, "isoformat"):
                                ts = ts.isoformat()
                        except Exception:
                            ts = str(ts)
                        payload = {
                            "id": int(r[0]),
                            "timestamp": ts,
                            "level": r[2],
                            "symbol": r[3],
                            "step": r[4],
                            "message": r[5],
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    yield ": keep-alive\n\n"

                # Non-blocking async sleep - allows other requests to be processed.
                await asyncio.sleep(float(poll_seconds))
        except GeneratorExit:
            return
        except Exception:
            return  # Exit gracefully on any error

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Prevent proxy buffering (best-effort; harmless when ignored)
            "X-Accel-Buffering": "no",
        },
    )


