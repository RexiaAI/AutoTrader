from __future__ import annotations

from src.broker.connection import IBConnection
from src.trader.market_hours import is_near_market_close
from src.utils.database import log_event, record_trade, update_live_status


def flatten_positions_if_needed(conn: IBConnection, minutes_before_close: int) -> None:
    """For intraday trading: attempt to flatten positions shortly before the market close."""
    try:
        portfolio = conn.ib.portfolio()
    except Exception as e:
        log_event("ERROR", f"Failed to load portfolio for flattening: {e}", symbol="IBKR", step="Flatten")
        return

    for p in portfolio:
        c = getattr(p, "contract", None)
        if c is None:
            continue
        sym = getattr(c, "symbol", None)
        exch = getattr(c, "exchange", None)
        cur = getattr(c, "currency", None)
        pos = float(getattr(p, "position", 0.0) or 0.0)
        if sym is None or pos == 0:
            continue

        if not is_near_market_close(exch or "", cur or "", minutes_before_close):
            continue

        try:
            update_live_status(sym, f"Flattening before close ({minutes_before_close}m)")
            log_event("INFO", f"Flattening position before close ({minutes_before_close}m)", symbol=sym, step="Flatten")

            # Cancel any open orders for this symbol first (e.g. bracket children).
            for t in conn.ib.openTrades():
                if getattr(getattr(t, "contract", None), "symbol", None) == sym:
                    try:
                        conn.ib.cancelOrder(getattr(t, "order", None))
                    except Exception:
                        pass

            action = "SELL" if pos > 0 else "BUY"
            qty = int(abs(pos))
            if qty > 0:
                from ib_insync import MarketOrder  # local import to avoid circularity

                trade = conn.ib.placeOrder(c, MarketOrder(action, qty))
                try:
                    mp = getattr(p, "marketPrice", None)
                    price = float(mp) if mp is not None else None
                except Exception:
                    price = None
                try:
                    order_status = getattr(getattr(trade, "orderStatus", None), "status", "Submitted")
                except Exception:
                    order_status = "Submitted"
                try:
                    record_trade(
                        sym,
                        action,
                        qty,
                        price,
                        stop_loss=None,
                        take_profit=None,
                        sentiment_score=None,
                        status=str(order_status),
                        rationale=f"Flatten before close ({minutes_before_close}m)",
                    )
                except Exception:
                    # Do not block flattening on logging issues
                    pass
        except Exception as e:
            log_event("ERROR", f"Flattening failed: {e}", symbol=sym, step="Flatten")


