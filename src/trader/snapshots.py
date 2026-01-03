from __future__ import annotations

import logging

from src.broker.connection import IBConnection
from src.utils.database import log_event, snapshot_open_orders, snapshot_positions

logger = logging.getLogger(__name__)


def snapshot_portfolio_and_orders(conn: IBConnection) -> None:
    """Snapshot portfolio positions + open orders into the DB for the dashboard."""
    # Positions (use portfolio() to get market values and P&L)
    try:
        portfolio = conn.ib.portfolio()
        pos_rows = []
        for p in portfolio:
            pos_rows.append(
                {
                    "account": getattr(p, "account", None),
                    "symbol": getattr(getattr(p, "contract", None), "symbol", None),
                    "exchange": getattr(getattr(p, "contract", None), "exchange", None),
                    "currency": getattr(getattr(p, "contract", None), "currency", None),
                    "position": float(getattr(p, "position", 0.0)),
                    "avg_cost": float(getattr(p, "averageCost", 0.0)),
                    "market_price": float(getattr(p, "marketPrice", 0.0)),
                    "market_value": float(getattr(p, "marketValue", 0.0)),
                    "unrealised_pnl": float(getattr(p, "unrealizedPNL", 0.0)),
                    "realised_pnl": float(getattr(p, "realizedPNL", 0.0)),
                }
            )
        snapshot_positions(pos_rows)
    except Exception as e:
        log_event("ERROR", f"Failed to snapshot positions: {e}", symbol="IBKR", step="Snapshot")

    # Open orders (trades)
    try:
        # ib_insync keeps an in-memory cache of trades/orders. Over long runtimes this can drift.
        # If it looks stale, clear cached state before refreshing from TWS via reqAllOpenOrders.
        try:
            cached_open = len(conn.ib.openTrades())
        except Exception:
            cached_open = 0
        if cached_open > 50:
            logger.warning(
                "Open orders cache looks stale (openTrades=%s). Clearing cached trades/orders before refresh.",
                cached_open,
            )
            try:
                conn.ib.trades().clear()
                conn.ib.orders().clear()
            except Exception:
                pass

        open_trades = conn.ib.reqAllOpenOrders() or []
        order_rows = []
        for t in open_trades:
            c = getattr(t, "contract", None)
            o = getattr(t, "order", None)
            s = getattr(t, "orderStatus", None)
            order_rows.append(
                {
                    "order_id": int(getattr(o, "orderId", 0) or 0),
                    "symbol": getattr(c, "symbol", None),
                    "exchange": getattr(c, "exchange", None),
                    "currency": getattr(c, "currency", None),
                    "action": getattr(o, "action", None),
                    "order_type": getattr(o, "orderType", None),
                    "total_qty": float(getattr(o, "totalQuantity", 0.0) or 0.0),
                    "filled": float(getattr(s, "filled", 0.0) or 0.0),
                    "remaining": float(getattr(s, "remaining", 0.0) or 0.0),
                    "status": getattr(s, "status", None),
                    "lmt_price": float(getattr(o, "lmtPrice", 0.0) or 0.0),
                    "aux_price": float(getattr(o, "auxPrice", 0.0) or 0.0),
                }
            )
        snapshot_open_orders(order_rows)
    except Exception as e:
        log_event("ERROR", f"Failed to snapshot open orders: {e}", symbol="IBKR", step="Snapshot")


