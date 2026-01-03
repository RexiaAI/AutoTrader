"""
AI-driven position management module.

Reviews open positions and decides whether to:
- HOLD: Keep position as-is
- SELL: Exit immediately (cancel orders, market sell)
- ADJUST_STOP: Modify stop-loss order
- ADJUST_TP: Modify take-profit order
"""
import logging
import time
from datetime import datetime
from typing import Any

from ib_insync import Stock

from src.research.ai_researcher import AIResearcher
from src.data.news import IBKRNewsFetcher
from src.data.retrieval import MarketData
from src.trading.executor import TradeExecutor
from src.utils.database import (
    log_event,
    log_position_review,
    mark_position_review_executed,
    log_order_review,
    mark_order_review_executed,
    record_trade,
    update_live_status,
    get_position_reviews_for_symbol,
    get_latest_reddit_sentiment_for_symbol,
)

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages open positions using AI to make hold/sell/adjust decisions.
    """

    def __init__(
        self,
        ib_connection,
        executor: TradeExecutor,
        data_engine: MarketData,
        ai_researcher: AIResearcher,
        config: dict,
    ):
        self.ib = ib_connection.ib
        self.conn = ib_connection
        self.executor = executor
        self.data_engine = data_engine
        self.ai_researcher = ai_researcher
        self.config = config
        self.news_fetcher = IBKRNewsFetcher(ib_connection)
        
        # Load position management config
        pm_cfg = config.get("position_management", {}) or {}
        # The system is AI-driven; position and order management are always on.
        self.enabled = True
        self.review_interval_seconds = pm_cfg.get("review_interval_seconds", 60)
        self.min_hold_minutes = pm_cfg.get("min_hold_minutes", 5)
        self.min_sell_confidence = pm_cfg.get("min_sell_confidence", 0.6)
        self.min_adjust_confidence = pm_cfg.get("min_adjust_confidence", 0.5)
        self.max_adjustments_per_position = pm_cfg.get("max_adjustments_per_position", 5)
        self.max_stop_widen_pct = pm_cfg.get("max_stop_widen_pct", 5.0)
        self.opportunity_rotation_enabled = pm_cfg.get("opportunity_rotation_enabled", True)
        self.min_score_advantage = pm_cfg.get("min_score_advantage_for_rotation", 0.2)
        
        # Trailing stop config
        ts_cfg = pm_cfg.get("trailing_stop", {})
        self.breakeven_trigger_pct = ts_cfg.get("breakeven_trigger_pct", 2.0)
        self.trail_start_pct = ts_cfg.get("trail_start_pct", 3.0)
        self.trail_lock_pct = ts_cfg.get("trail_lock_pct", 50.0)
        
        # Track entry times and adjustment counts (symbol -> data)
        self._position_metadata: dict[str, dict] = {}
        # Last review time per symbol
        self._last_review: dict[str, float] = {}

    def _get_position_entry_time(self, symbol: str) -> datetime | None:
        """Get when we entered this position.

        Uses cached metadata first, then falls back to the database.
        Handles PostgreSQL/pandas timestamp types and ISO strings.
        """
        if symbol in self._position_metadata:
            return self._position_metadata[symbol].get("entry_time")

        # Try database
        from src.utils.database import get_last_trade_for_symbol

        last_buy = get_last_trade_for_symbol(symbol, "BUY")
        if not last_buy:
            return None

        ts = last_buy.get("timestamp")
        if ts is None:
            return None

        # pandas / datetime objects
        try:
            import pandas as pd  # type: ignore

            if isinstance(ts, pd.Timestamp):
                return ts.to_pydatetime()
        except Exception:
            pass

        if isinstance(ts, datetime):
            return ts

        # Strings (SQLite or ISO formats)
        ts_str = str(ts).strip()
        if not ts_str:
            return None

        # Normalise common ISO Z suffix
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"

        try:
            # Handles both 'YYYY-MM-DD HH:MM:SS(.ffffff)' and ISO variants
            return datetime.fromisoformat(ts_str)
        except Exception:
            pass

        # Fallback formats
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(ts_str, fmt)
            except Exception:
                continue

        return None

    def _record_position_entry(self, symbol: str, entry_price: float) -> None:
        """Record when a position was entered."""
        self._position_metadata[symbol] = {
            "entry_time": datetime.now(),
            "entry_price": entry_price,
            "adjustment_count": 0,
            "peak_pnl_pct": 0.0,
            "peak_price": entry_price,
        }

    def _get_adjustment_count(self, symbol: str) -> int:
        """Get how many times we've adjusted orders for this position today."""
        if symbol in self._position_metadata:
            return self._position_metadata[symbol].get("adjustment_count", 0)
        return 0

    def _increment_adjustment_count(self, symbol: str) -> None:
        """Increment adjustment count for a position."""
        if symbol not in self._position_metadata:
            self._position_metadata[symbol] = {}
        self._position_metadata[symbol]["adjustment_count"] = (
            self._position_metadata[symbol].get("adjustment_count", 0) + 1
        )

    def _clear_position_metadata(self, symbol: str) -> None:
        """Clear metadata when position is closed."""
        self._position_metadata.pop(symbol, None)
        self._last_review.pop(symbol, None)

    def review_all_positions(
        self,
        top_candidates: list[dict] | None = None,
        market_context: dict | None = None,
    ) -> list[dict]:
        """
        Review all open positions and return list of actions taken.

        Args:
            top_candidates: Best candidates from latest research (for opportunity comparison)
            market_context: Current market context (SPY/QQQ performance)

        Returns:
            List of dicts with {symbol, action, executed, rationale}
        """
        if not self.enabled:
            return []

        results = []
        positions = self.ib.positions()

        if not positions:
            logger.debug("No open positions to review")
            return results

        update_live_status("Position Manager", f"Reviewing {len(positions)} positions")
        log_event("INFO", f"Reviewing {len(positions)} open positions", symbol="PM", step="Start")

        for pos in positions:
            try:
                result = self._review_single_position(
                    pos,
                    top_candidates=top_candidates,
                    market_context=market_context,
                )
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Error reviewing position {pos.contract.symbol}: {e}")
                log_event("ERROR", f"Position review failed: {e}", symbol=pos.contract.symbol, step="PM")

        update_live_status("Position Manager", f"Reviewed {len(positions)} positions, {len(results)} actions")
        return results

    def _review_single_position(
        self,
        position,
        top_candidates: list[dict] | None = None,
        market_context: dict | None = None,
    ) -> dict | None:
        """
        Review a single position and potentially take action.

        Returns dict with action details if action was taken/suggested, None otherwise.
        """
        contract = position.contract
        symbol = contract.symbol
        quantity = int(position.position)
        avg_cost = float(position.avgCost)

        # Skip short positions (we only manage longs for now)
        if quantity <= 0:
            return None

        # Check if we recently reviewed this position
        now = time.time()
        last_review = self._last_review.get(symbol, 0)
        if now - last_review < self.review_interval_seconds:
            return None  # Too soon to review again

        self._last_review[symbol] = now

        # Get current market data from portfolio (already contains marketPrice)
        # This is faster and more reliable than reqMktData
        portfolio_items = self.ib.portfolio()
        portfolio_item = next(
            (p for p in portfolio_items if p.contract.conId == contract.conId),
            None
        )
        
        if portfolio_item:
            current_price = float(portfolio_item.marketPrice)
            unrealised_pnl = float(portfolio_item.unrealizedPNL)
            pnl_pct = ((current_price - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 0
        else:
            # Fallback to reqMktData if not in portfolio (shouldn't happen for open positions)
            self.ib.qualifyContracts(contract)
            ticker = self.ib.reqMktData(contract, '', False, False)
            self.ib.sleep(2)  # Wait longer for data

            current_price = ticker.marketPrice()
            if current_price is None or current_price != current_price:  # NaN check
                current_price = ticker.last or ticker.close or avg_cost
                if current_price is None or current_price != current_price:
                    logger.warning(f"Cannot get current price for {symbol}")
                    return None
            
            unrealised_pnl = (current_price - avg_cost) * quantity
            pnl_pct = ((current_price - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 0

        # Get time held
        entry_time = self._get_position_entry_time(symbol)
        if entry_time:
            minutes_held = int((datetime.now() - entry_time).total_seconds() / 60)
        else:
            # Estimate from position metadata
            minutes_held = 30  # Default assumption
            self._record_position_entry(symbol, avg_cost)

        # Ensure metadata exists so we can track peak P&L / drawdown
        if symbol not in self._position_metadata:
            self._record_position_entry(symbol, avg_cost)

        # Update peak P&L tracking (data for the AI, not a rule)
        meta = self._position_metadata.get(symbol, {})
        try:
            peak_pnl_pct = float(meta.get("peak_pnl_pct", pnl_pct))
        except Exception:
            peak_pnl_pct = pnl_pct
        if pnl_pct > peak_pnl_pct:
            peak_pnl_pct = pnl_pct
            meta["peak_pnl_pct"] = peak_pnl_pct
        try:
            peak_price = float(meta.get("peak_price", current_price))
        except Exception:
            peak_price = current_price
        if current_price > peak_price:
            peak_price = current_price
            meta["peak_price"] = peak_price
        drawdown_from_peak_pct = round(float(peak_pnl_pct) - float(pnl_pct), 2)

        # Get current order levels
        orders_summary = self.executor.get_orders_summary_for_symbol(symbol)
        current_stop = orders_summary.get("stop_loss")
        current_tp = orders_summary.get("take_profit")

        # Calculate distances
        distance_to_stop_pct = None
        distance_to_tp_pct = None
        if current_stop is not None:
            distance_to_stop_pct = ((current_price - current_stop) / current_price) * 100
        if current_tp is not None:
            distance_to_tp_pct = ((current_tp - current_price) / current_price) * 100

        # Get technical indicators
        indicators = {}
        bar_momentum = {}
        try:
            bar_size = self.config.get("intraday", {}).get("bar_size", "5 mins")
            duration = self.config.get("intraday", {}).get("duration", "2 D")
            use_rth = self.config.get("intraday", {}).get("use_rth", True)
            
            bars = self.data_engine.fetch_historical_data(
                symbol=symbol,
                exchange=contract.exchange or "SMART",
                currency=contract.currency or "USD",
                duration=duration,
                bar_size=bar_size,
                use_rth=use_rth,
            )
            
            if bars is not None and len(bars) >= 14:
                from src.research.analyser import ResearchAnalyser
                analyser = ResearchAnalyser(self.config)
                df_ind = analyser.apply_indicators(bars)
                if df_ind is not None and len(df_ind) > 0:
                    last = df_ind.iloc[-1]
                    rsi_val = last.get("RSI_14")
                    atr_val = None
                    atr_cols = [c for c in df_ind.columns if str(c).startswith("ATRr_")]
                    if atr_cols:
                        atr_val = last.get(atr_cols[0])
                    indicators = {
                        "rsi_14": float(rsi_val) if rsi_val is not None else None,
                        "atr": float(atr_val) if atr_val is not None else None,
                    }
                    # Bar momentum
                    if len(df_ind) >= 10:
                        bar_momentum = analyser.calculate_bar_momentum(df_ind)
        except Exception as e:
            logger.warning(f"Failed to get indicators for {symbol}: {e}")

        # Normalise empty dicts to None (avoid misleading "empty but present" payloads)
        if not indicators:
            indicators = {}
        if not bar_momentum:
            bar_momentum = None

        # Get real headlines (no fabricated data)
        headlines: list[str] = []
        try:
            self.ib.qualifyContracts(contract)
            headlines = self.news_fetcher.fetch_headlines(contract, lookback_days=1, limit=8)
        except Exception as e:
            log_event("WARN", f"IBKR headlines unavailable: {e}", symbol=symbol, step="PM-News")
            headlines = []

        # Get cached Reddit sentiment (if available)
        reddit = None
        try:
            row = get_latest_reddit_sentiment_for_symbol(symbol)
            if row:
                reddit = {
                    "mentions": int(row.get("mentions") or 0),
                    "sentiment": float(row.get("sentiment")) if row.get("sentiment") is not None else None,
                    "confidence": float(row.get("confidence")) if row.get("confidence") is not None else None,
                    "rationale": row.get("rationale"),
                    "source_fetch_utc": int(row.get("source_fetch_utc") or 0),
                }
        except Exception as e:
            log_event("WARN", f"Reddit sentiment unavailable: {e}", symbol=symbol, step="PM-Reddit")
            reddit = None

        # Basic liquidity/fundamentals snapshot (best-effort, no fabrication)
        fundamentals = None
        try:
            t = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(0.5)
            vol = getattr(t, "volume", None)
            av = getattr(t, "avVolume", None)
            rel_vol = None
            if vol is not None and av is not None and float(av) > 0:
                rel_vol = round(float(vol) / float(av), 2)
            bid = getattr(t, "bid", None)
            ask = getattr(t, "ask", None)
            spread_pct = None
            if bid is not None and ask is not None and float(ask) > 0:
                spread_pct = round(((float(ask) - float(bid)) / float(ask)) * 100, 2)
            fundamentals = {
                "volume": float(vol) if vol is not None else None,
                "avg_volume": float(av) if av is not None else None,
                "relative_volume": rel_vol,
                "bid": float(bid) if bid is not None else None,
                "ask": float(ask) if ask is not None else None,
                "spread_pct": spread_pct,
                "day_high": float(getattr(t, "high", None)) if getattr(t, "high", None) is not None else None,
                "day_low": float(getattr(t, "low", None)) if getattr(t, "low", None) is not None else None,
            }
        except Exception as e:
            log_event("WARN", f"Liquidity snapshot unavailable: {e}", symbol=symbol, step="PM-Liq")
            fundamentals = None
        finally:
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                pass

        # Prepare top candidates for comparison (if opportunity rotation enabled)
        top_cands_for_ai = None
        if self.opportunity_rotation_enabled and top_candidates:
            top_cands_for_ai = [
                {"symbol": c.get("symbol"), "score": c.get("score"), "rationale": c.get("rationale", "")}
                for c in top_candidates[:5]
            ]

        # Call AI to review position
        log_event("INFO", f"AI reviewing position ({pnl_pct:.1f}%)", symbol=symbol, step="PM-AI")

        try:
            ai_decision = self.ai_researcher.review_position(
                symbol=symbol,
                exchange=contract.exchange or "SMART",
                currency=contract.currency or "USD",
                entry_price=avg_cost,
                current_price=current_price,
                quantity=quantity,
                unrealised_pnl=unrealised_pnl,
                pnl_pct=pnl_pct,
                peak_pnl_pct=peak_pnl_pct,
                drawdown_from_peak_pct=drawdown_from_peak_pct,
                minutes_held=minutes_held,
                current_stop_loss=current_stop,
                current_take_profit=current_tp,
                distance_to_stop_pct=distance_to_stop_pct,
                distance_to_tp_pct=distance_to_tp_pct,
                indicators=indicators,
                bar_momentum=bar_momentum,
                fundamentals=fundamentals,
                market_context=market_context,
                headlines=headlines,
                reddit=reddit,
                top_candidates=top_cands_for_ai,
                intraday=self.config.get("intraday"),
            )
        except Exception as e:
            logger.error(f"AI position review failed for {symbol}: {e}")
            log_event("ERROR", f"AI review failed: {e}", symbol=symbol, step="PM-AI")
            return None

        action = ai_decision["action"]
        confidence = ai_decision["confidence"]
        urgency = ai_decision["urgency"]
        rationale = ai_decision["rationale"]
        new_stop = ai_decision.get("new_stop_loss")
        new_tp = ai_decision.get("new_take_profit")
        key_factors = ai_decision.get("key_factors", [])

        # Log the decision
        review_id = log_position_review(
            symbol=symbol,
            exchange=contract.exchange,
            currency=contract.currency,
            entry_price=avg_cost,
            current_price=current_price,
            quantity=quantity,
            unrealised_pnl=unrealised_pnl,
            pnl_pct=pnl_pct,
            minutes_held=minutes_held,
            current_stop_loss=current_stop,
            current_take_profit=current_tp,
            action=action,
            new_stop_loss=new_stop,
            new_take_profit=new_tp,
            confidence=confidence,
            urgency=urgency,
            rationale=rationale,
            key_factors=key_factors,
            executed=False,
        )

        # Decide whether to execute
        executed = False
        execution_result = None

        if action == "HOLD":
            log_event("INFO", f"AI: HOLD (conf {confidence:.2f}) — {rationale}", symbol=symbol, step="PM-Hold")
            # No action needed
            
        elif action == "SELL":
            # Check for existing pending SELL orders to prevent duplicates
            existing_sells = self.executor.get_pending_sell_orders_for_symbol(symbol)
            if existing_sells:
                log_event(
                    "WARN",
                    f"AI: SELL suggested but {len(existing_sells)} SELL order(s) already pending",
                    symbol=symbol,
                    step="PM-Skip",
                )
            else:
                log_event("INFO", f"AI: SELL (conf {confidence:.2f}) — {rationale}", symbol=symbol, step="PM-Sell")
                try:
                    trade = self.executor.sell_position(contract, quantity)
                    if trade:
                        executed = True
                        execution_result = "SOLD"
                        self._clear_position_metadata(symbol)
                        mark_position_review_executed(review_id)
                        # Record the SELL in the trades table
                        record_trade(
                            symbol=symbol,
                            action="SELL",
                            quantity=quantity,
                            price=current_price,
                            stop_loss=None,
                            take_profit=None,
                            sentiment_score=confidence,
                            status="EXECUTED",
                            rationale=rationale,
                        )
                        log_event("INFO", f"Executed SELL for {quantity} shares", symbol=symbol, step="PM-Sell")
                except Exception as e:
                    log_event("ERROR", f"SELL failed: {e}", symbol=symbol, step="PM-Sell")

        elif action == "ADJUST_STOP":
            if new_stop is None:
                log_event("WARN", "AI: ADJUST_STOP returned null new_stop_loss", symbol=symbol, step="PM-Adjust")
            else:
                # Basic correctness: stop must be a positive price below current price for a long position
                if new_stop <= 0 or new_stop >= current_price:
                    log_event(
                        "WARN",
                        f"AI: ADJUST_STOP invalid new_stop_loss={new_stop:.4f} vs current_price={current_price:.4f}",
                        symbol=symbol,
                        step="PM-Adjust",
                    )
                else:
                    log_event("INFO", f"AI: ADJUST_STOP to {new_stop:.2f} (conf {confidence:.2f})", symbol=symbol, step="PM-Adjust")
                    try:
                        success = self.executor.upsert_stop_loss(contract, new_stop, quantity=quantity)
                        if success:
                            executed = True
                            execution_result = f"STOP -> {new_stop:.2f}"
                            self._increment_adjustment_count(symbol)
                            mark_position_review_executed(review_id)
                    except Exception as e:
                        log_event("ERROR", f"ADJUST_STOP failed: {e}", symbol=symbol, step="PM-Adjust")

        elif action == "ADJUST_TP":
            if new_tp is None:
                log_event("WARN", "AI: ADJUST_TP returned null new_take_profit", symbol=symbol, step="PM-Adjust")
            else:
                # Basic correctness for a take-profit limit: should be above current market
                if new_tp <= current_price:
                    log_event(
                        "WARN",
                        f"AI: ADJUST_TP invalid new_take_profit={new_tp:.4f} <= current_price={current_price:.4f}",
                        symbol=symbol,
                        step="PM-Adjust",
                    )
                else:
                    log_event("INFO", f"AI: ADJUST_TP to {new_tp:.2f} (conf {confidence:.2f})", symbol=symbol, step="PM-Adjust")
                    try:
                        success = self.executor.upsert_take_profit(contract, new_tp, quantity=quantity)
                        if success:
                            executed = True
                            execution_result = f"TP -> {new_tp:.2f}"
                            self._increment_adjustment_count(symbol)
                            mark_position_review_executed(review_id)
                    except Exception as e:
                        log_event("ERROR", f"ADJUST_TP failed: {e}", symbol=symbol, step="PM-Adjust")

        return {
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "urgency": urgency,
            "executed": executed,
            "execution_result": execution_result,
            "rationale": rationale,
            "pnl_pct": pnl_pct,
            "review_id": review_id,
        }

    # ----- ORDER REVIEW METHODS -----

    def review_open_orders(
        self,
        min_age_minutes: int = 0,
        market_context: dict | None = None,
    ) -> list[dict]:
        """
        Review all open orders and decide whether to KEEP, CANCEL, or ADJUST their prices.

        Args:
            min_age_minutes: Only review orders older than this (avoids reviewing fresh orders)
            market_context: Current market context (SPY/QQQ performance)

        Returns:
            List of dicts with {symbol, order_id, action, executed, rationale}
        """
        if not self.enabled:
            return []

        results = []
        open_orders = self.executor.get_all_open_orders_with_details()

        if not open_orders:
            logger.debug("No open orders to review")
            return results

        # Filter to orders that are old enough to review
        # Skip stop-loss orders for positions we hold (those are intentional protection)
        orders_to_review = []
        positions = {p.contract.symbol for p in self.ib.positions() if p.position > 0}

        for order_info in open_orders:
            age = order_info.get("age_minutes", 0)
            symbol = order_info.get("symbol")
            order_type = order_info.get("order_type")
            action = order_info.get("action")
            parent_id = order_info.get("parent_id", 0)

            # Skip very fresh orders
            if age < min_age_minutes:
                continue

            # Skip child orders (part of bracket) - only review parent/standalone
            if parent_id != 0:
                continue

            orders_to_review.append(order_info)

        if not orders_to_review:
            logger.debug("No orders old enough to review")
            return results

        update_live_status("Order Manager", f"Reviewing {len(orders_to_review)} open orders")
        log_event("INFO", f"Reviewing {len(orders_to_review)} open orders", symbol="OM", step="Start")

        for order_info in orders_to_review:
            try:
                result = self._review_single_order(order_info, market_context)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Error reviewing order {order_info.get('order_id')}: {e}")
                log_event("ERROR", f"Order review failed: {e}", symbol=order_info.get("symbol"), step="OM")

        update_live_status("Order Manager", f"Reviewed {len(orders_to_review)} orders, {len(results)} actions")
        return results

    def _review_single_order(
        self,
        order_info: dict,
        market_context: dict | None = None,
    ) -> dict | None:
        """
        Review a single order and potentially take action.

        Returns dict with action details if action was taken, None otherwise.
        """
        order_id = order_info["order_id"]
        symbol = order_info["symbol"]
        contract = order_info["contract"]
        order_action = order_info["action"]  # BUY or SELL
        order_type = order_info["order_type"]  # LMT, STP, MKT
        order_quantity = order_info["quantity"]
        order_price = order_info["order_price"]
        age_minutes = order_info["age_minutes"]

        # Get current market data
        self.ib.qualifyContracts(contract)
        ticker = None
        try:
            ticker = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(1)

            current_price = ticker.marketPrice()
            bid_price = ticker.bid
            ask_price = ticker.ask

            # Handle NaN/None
            if current_price is None or current_price != current_price:
                current_price = ticker.last or ticker.close
                if current_price is None or current_price != current_price:
                    # Market data may be unavailable (no subscription). Do not fabricate:
                    # proceed with current_price=None and let the AI decide based on order details.
                    logger.warning(f"Cannot get current price for order {order_id} ({symbol}); continuing without market price")
                    current_price = None
                    bid_price = None
                    ask_price = None
        finally:
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                pass

        # Calculate distance
        price_distance_pct = None
        if order_price is not None and current_price is not None and current_price > 0:
            price_distance_pct = ((order_price - current_price) / current_price) * 100

        # Call AI to review order
        log_event("INFO", f"AI reviewing order {order_id} ({order_action} {order_type})", symbol=symbol, step="OM-AI")

        try:
            ai_decision = self.ai_researcher.review_order(
                symbol=symbol,
                order_id=order_id,
                order_type=order_action,  # BUY or SELL
                order_side=order_type,  # LMT, STP
                order_quantity=order_quantity,
                order_price=order_price,
                current_price=current_price,
                bid_price=bid_price if bid_price and bid_price == bid_price else None,
                ask_price=ask_price if ask_price and ask_price == ask_price else None,
                order_age_minutes=age_minutes,
                market_context=market_context,
            )
        except Exception as e:
            logger.error(f"AI order review failed for {symbol} order {order_id}: {e}")
            log_event("ERROR", f"AI order review failed: {e}", symbol=symbol, step="OM-AI")
            return None

        action = ai_decision["action"]
        new_price = ai_decision.get("new_price")
        confidence = ai_decision["confidence"]
        rationale = ai_decision["rationale"]

        # Log the decision
        review_id = log_order_review(
            order_id=order_id,
            symbol=symbol,
            order_type=order_type,
            order_action=order_action,
            order_quantity=order_quantity,
            order_price=order_price,
            current_price=current_price,
            bid_price=bid_price if bid_price and bid_price == bid_price else None,
            ask_price=ask_price if ask_price and ask_price == ask_price else None,
            price_distance_pct=price_distance_pct,
            order_age_minutes=age_minutes,
            action=action,
            new_price=new_price,
            confidence=confidence,
            rationale=rationale,
            executed=False,
        )

        # Execute the decision
        executed = False
        execution_result = None

        if action == "KEEP":
            log_event("INFO", f"AI: KEEP order {order_id} (conf {confidence:.2f}) — {rationale}", symbol=symbol, step="OM-Keep")

        elif action == "CANCEL":
            log_event("INFO", f"AI: CANCEL order {order_id} (conf {confidence:.2f}) — {rationale}", symbol=symbol, step="OM-Cancel")
            try:
                success = self.executor.cancel_order(order_id)
                if success:
                    executed = True
                    execution_result = "CANCELLED"
                    mark_order_review_executed(review_id)
                    log_event("INFO", f"Cancelled order {order_id}", symbol=symbol, step="OM-Cancel")
            except Exception as e:
                log_event("ERROR", f"Cancel order failed: {e}", symbol=symbol, step="OM-Cancel")

        elif action == "ADJUST_PRICE":
            if new_price is None or new_price <= 0:
                log_event("WARN", f"AI: ADJUST_PRICE invalid new_price={new_price}", symbol=symbol, step="OM-Adjust")
            else:
                log_event("INFO", f"AI: ADJUST_PRICE order {order_id} to {new_price:.2f} (conf {confidence:.2f})", symbol=symbol, step="OM-Adjust")
                try:
                    success = self.executor.adjust_order_price(order_id, new_price)
                    if success:
                        executed = True
                        execution_result = f"PRICE -> {new_price:.2f}"
                        mark_order_review_executed(review_id)
                except Exception as e:
                    log_event("ERROR", f"Adjust order price failed: {e}", symbol=symbol, step="OM-Adjust")

        return {
            "order_id": order_id,
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "executed": executed,
            "execution_result": execution_result,
            "rationale": rationale,
            "review_id": review_id,
        }

