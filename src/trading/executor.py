from ib_insync import MarketOrder, StopOrder, LimitOrder, Stock
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import logging

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(self, ib_connection, config):
        self.ib = ib_connection.ib
        self.config = config
        self.risk_per_trade = config['trading']['risk_per_trade']
        self._min_tick_cache: dict[int | str, float] = {}

    def _req_all_open_trades(self):
        """
        Retrieve ALL open orders/trades currently visible in TWS (across clientIds).
        This avoids missing orders after restarts or when orders were created by another clientId.
        """
        try:
            return self.ib.reqAllOpenOrders()
        except Exception as e:
            logger.warning(f"Failed to reqAllOpenOrders(): {e}")
            # Fall back to what this session currently knows.
            return self.ib.openTrades()

    def calculate_position_size(self, price, stop_loss_price, net_liquidation=None):
        """Calculates the number of shares to buy based on risk management."""
        if net_liquidation is None:
            account_summary = self.ib.accountSummary()
            net_liquidation = float([v.value for v in account_summary if v.tag == 'NetLiquidation'][0])
        
        cash_risk = net_liquidation * self.risk_per_trade
        risk_per_share = abs(price - stop_loss_price)
        
        if risk_per_share == 0:
            return 0
            
        quantity = int(cash_risk / risk_per_share)
        return quantity

    def _get_min_tick(self, contract) -> float | None:
        """Best-effort lookup of the contract min tick (cached)."""
        con_id = getattr(contract, "conId", None)
        if con_id:
            key: int | str = int(con_id)
        else:
            key = f"{getattr(contract, 'symbol', '')}:{getattr(contract, 'exchange', '')}:{getattr(contract, 'currency', '')}"

        if key in self._min_tick_cache:
            return self._min_tick_cache[key]

        try:
            details = self.ib.reqContractDetails(contract)
            if details:
                min_tick = float(getattr(details[0], "minTick", 0.0) or 0.0)
                if min_tick > 0:
                    self._min_tick_cache[key] = min_tick
                    return min_tick
        except Exception as e:
            logger.debug(f"Failed to fetch contract details for minTick: {e}")

        return None

    def _round_price_down_to_tick(self, price: float, min_tick: float) -> float:
        """Round price DOWN to the nearest valid tick (useful for SELL stops)."""
        p = Decimal(str(price))
        t = Decimal(str(min_tick))
        if t <= 0:
            return float(price)
        steps = (p / t).to_integral_value(rounding=ROUND_FLOOR)
        return float(steps * t)

    def _round_price_up_to_tick(self, price: float, min_tick: float) -> float:
        """Round price UP to the nearest valid tick (useful for SELL take-profits)."""
        p = Decimal(str(price))
        t = Decimal(str(min_tick))
        if t <= 0:
            return float(price)
        steps = (p / t).to_integral_value(rounding=ROUND_CEILING)
        return float(steps * t)

    # ----- Order Management Methods -----

    def get_open_orders_for_symbol(self, symbol: str) -> list:
        """Get all open orders for a specific symbol."""
        trades = self._req_all_open_trades()
        out = []
        for t in trades:
            try:
                if t.contract.symbol == symbol:
                    out.append(t.order)
            except Exception:
                continue
        return out

    def get_open_trades_for_symbol(self, symbol: str) -> list:
        """Get all open trades for a specific symbol."""
        all_trades = self._req_all_open_trades()
        return [t for t in all_trades if getattr(t.contract, "symbol", None) == symbol]

    def cancel_orders_for_symbol(self, symbol: str) -> int:
        """Cancel all open orders for a specific symbol. Returns count of orders cancelled."""
        trades = self.get_open_trades_for_symbol(symbol)
        cancelled = 0
        for trade in trades:
            try:
                self.ib.cancelOrder(trade.order)
                cancelled += 1
                logger.info(f"Cancelled order {trade.order.orderId} for {symbol}")
            except Exception as e:
                logger.warning(f"Failed to cancel order {trade.order.orderId} for {symbol}: {e}")
        return cancelled

    def sell_position(self, contract, quantity: int) -> object | None:
        """
        Execute a market sell order to close a position.
        Cancels any existing orders for this symbol first.
        
        SAFETY: Will not sell more than we actually own to prevent short positions.
        """
        if quantity <= 0:
            logger.warning(f"Invalid sell quantity {quantity} for {contract.symbol}")
            return None

        # SAFETY CHECK: Get actual position quantity to prevent shorting
        actual_position = self.get_position_quantity(contract.symbol)
        if actual_position <= 0:
            logger.warning(f"Cannot sell {contract.symbol}: no long position held (qty: {actual_position})")
            return None
        
        # Cap sell quantity to what we actually own
        safe_quantity = min(quantity, actual_position)
        if safe_quantity < quantity:
            logger.warning(f"Capping sell quantity from {quantity} to {safe_quantity} (actual position)")
        
        # Cancel existing orders first
        self.cancel_orders_for_symbol(contract.symbol)
        self.ib.sleep(0.5)  # Brief pause for order cancellations to process

        sell_order = MarketOrder('SELL', safe_quantity)
        sell_order.transmit = True  # Ensure immediate transmission
        trade = self.ib.placeOrder(contract, sell_order)
        logger.info(f"Placed MARKET SELL order for {safe_quantity} shares of {contract.symbol}")
        return trade
    
    def get_position_quantity(self, symbol: str) -> int:
        """Get the current position quantity for a symbol. Returns 0 if no position."""
        positions = self.ib.positions()
        for pos in positions:
            if pos.contract.symbol == symbol:
                return int(pos.position)
        return 0
    
    def close_short_position(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> object | None:
        """
        Close a short position by buying to cover.
        Returns the trade object if successful, None otherwise.
        """
        position_qty = self.get_position_quantity(symbol)
        if position_qty >= 0:
            logger.warning(f"Cannot close short for {symbol}: not a short position (qty: {position_qty})")
            return None
        
        # Buy to cover: need to buy the absolute value of the negative position
        buy_quantity = abs(position_qty)
        
        # Create contract
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)
        
        # Cancel any existing orders first
        self.cancel_orders_for_symbol(symbol)
        self.ib.sleep(0.5)
        
        buy_order = MarketOrder('BUY', buy_quantity)
        buy_order.transmit = True
        trade = self.ib.placeOrder(contract, buy_order)
        logger.info(f"Placed BUY TO COVER order for {buy_quantity} shares of {symbol}")
        return trade

    def close_all_shorts(self) -> list[tuple[str, int]]:
        """
        Detect and close all short positions (negative quantities).
        Returns a list of (symbol, quantity) tuples for positions that had buy orders placed.
        
        This is a safety net - shorts should never occur in a long-only strategy.
        """
        positions = self.ib.positions()
        closed = []
        
        for pos in positions:
            qty = int(pos.position)
            if qty < 0:
                symbol = pos.contract.symbol
                logger.warning(f"SAFETY: Found short position {symbol} qty={qty}, closing...")
                trade = self.close_short_position(
                    symbol,
                    exchange=pos.contract.exchange or "SMART",
                    currency=pos.contract.currency or "USD",
                )
                if trade:
                    closed.append((symbol, abs(qty)))
        
        return closed

    def cancel_orphaned_sell_orders(self) -> int:
        """
        Cancel SELL orders for symbols where we have no long position.
        These orphaned orders can create accidental shorts when filled.
        Returns the count of orders cancelled.
        """
        # Get current long positions
        positions = self.ib.positions()
        long_symbols = {p.contract.symbol for p in positions if p.position > 0}
        
        trades = self._req_all_open_trades()
        cancelled = 0
        
        for trade in trades:
            order = trade.order
            if getattr(order, "action", None) == "SELL":
                symbol = getattr(trade.contract, "symbol", None)
                if symbol and symbol not in long_symbols:
                    logger.warning(f"SAFETY: Cancelling orphaned SELL order for {symbol} (no long position)")
                    try:
                        self.ib.cancelOrder(order)
                        cancelled += 1
                    except Exception as e:
                        logger.warning(f"Failed to cancel orphaned order for {symbol}: {e}")
        
        return cancelled

    def upsert_stop_loss(self, contract, stop_price: float, quantity: int | None = None) -> bool:
        """
        Create or update a protective stop-loss (SELL STP) for a long position.

        - If a stop-loss already exists: update its price.
        - If none exists: place a new stop-loss order.

        IMPORTANT: If a take-profit (SELL LMT) exists, we link stop/TP into an OCA group
        so that filling one cancels the other (prevents accidental short positions).
        """
        symbol = getattr(contract, "symbol", None) or ""
        if not symbol:
            logger.warning("Cannot upsert stop-loss: contract has no symbol")
            return False
        # Round to valid tick size where possible
        min_tick = self._get_min_tick(contract)
        if min_tick:
            rounded = self._round_price_down_to_tick(float(stop_price), min_tick)
            if rounded != float(stop_price):
                logger.info(f"Rounded stop for {symbol} to tick: {stop_price} -> {rounded} (minTick={min_tick})")
            stop_price = float(rounded)


        trades = self.get_open_trades_for_symbol(symbol)
        stop_trades = [t for t in trades if getattr(t.order, "orderType", None) == "STP" and getattr(t.order, "action", None) == "SELL"]
        tp_trades = [t for t in trades if getattr(t.order, "orderType", None) == "LMT" and getattr(t.order, "action", None) == "SELL"]

        # If we already have a stop, modify it
        if stop_trades:
            trade = stop_trades[0]
            order = trade.order
            old_price = getattr(order, "auxPrice", None)
            order.auxPrice = float(stop_price)

            # Link with TP if present
            if tp_trades:
                oca_group = f"OCA_EXIT_{symbol}"
                order.ocaGroup = oca_group
                order.ocaType = 1
                for extra in tp_trades[1:]:
                    try:
                        self.ib.cancelOrder(extra.order)
                    except Exception as e:
                        logger.warning(f"Failed to cancel extra TP order for {symbol}: {e}")
                tp_order = tp_trades[0].order
                tp_order.ocaGroup = oca_group
                tp_order.ocaType = 1
                self.ib.placeOrder(tp_trades[0].contract, tp_order)

            self.ib.placeOrder(trade.contract, order)
            logger.info(f"Modified STOP LOSS for {symbol}: {old_price} -> {stop_price}")

            # Cancel any extra stop orders
            for extra in stop_trades[1:]:
                try:
                    self.ib.cancelOrder(extra.order)
                except Exception as e:
                    logger.warning(f"Failed to cancel extra stop order for {symbol}: {e}")

            return True

        # Otherwise, create a new stop-loss
        qty = int(quantity) if quantity is not None else int(self.get_position_quantity(symbol))
        if qty <= 0:
            logger.warning(f"Cannot create stop-loss for {symbol}: no long position quantity available")
            return False

        sl_order = StopOrder("SELL", qty, float(stop_price))
        sl_order.transmit = True

        if tp_trades:
            # Link the new stop with existing TP via OCA
            oca_group = f"OCA_EXIT_{symbol}"
            sl_order.ocaGroup = oca_group
            sl_order.ocaType = 1
            for extra in tp_trades[1:]:
                try:
                    self.ib.cancelOrder(extra.order)
                except Exception as e:
                    logger.warning(f"Failed to cancel extra TP order for {symbol}: {e}")
            tp_order = tp_trades[0].order
            tp_order.ocaGroup = oca_group
            tp_order.ocaType = 1
            self.ib.placeOrder(tp_trades[0].contract, tp_order)

        self.ib.placeOrder(contract, sl_order)
        logger.info(f"Placed STOP LOSS for {symbol}: {stop_price} (qty={qty})")
        return True

    def upsert_take_profit(self, contract, take_profit_price: float, quantity: int | None = None) -> bool:
        """
        Create or update a take-profit (SELL LMT) for a long position.

        IMPORTANT: If a stop-loss (SELL STP) exists, we link stop/TP into an OCA group
        so that filling one cancels the other (prevents accidental short positions).
        """
        symbol = getattr(contract, "symbol", None) or ""
        if not symbol:
            logger.warning("Cannot upsert take-profit: contract has no symbol")
            return False
        # Round to valid tick size where possible
        min_tick = self._get_min_tick(contract)
        if min_tick:
            rounded = self._round_price_up_to_tick(float(take_profit_price), min_tick)
            if rounded != float(take_profit_price):
                logger.info(f"Rounded take-profit for {symbol} to tick: {take_profit_price} -> {rounded} (minTick={min_tick})")
            take_profit_price = float(rounded)


        trades = self.get_open_trades_for_symbol(symbol)
        stop_trades = [t for t in trades if getattr(t.order, "orderType", None) == "STP" and getattr(t.order, "action", None) == "SELL"]
        tp_trades = [t for t in trades if getattr(t.order, "orderType", None) == "LMT" and getattr(t.order, "action", None) == "SELL"]

        # If we already have a TP, modify it
        if tp_trades:
            trade = tp_trades[0]
            order = trade.order
            old_price = getattr(order, "lmtPrice", None)
            order.lmtPrice = float(take_profit_price)

            # Link with stop if present
            if stop_trades:
                oca_group = f"OCA_EXIT_{symbol}"
                order.ocaGroup = oca_group
                order.ocaType = 1
                for extra in stop_trades[1:]:
                    try:
                        self.ib.cancelOrder(extra.order)
                    except Exception as e:
                        logger.warning(f"Failed to cancel extra stop order for {symbol}: {e}")
                sl_order = stop_trades[0].order
                sl_order.ocaGroup = oca_group
                sl_order.ocaType = 1
                self.ib.placeOrder(stop_trades[0].contract, sl_order)

            self.ib.placeOrder(trade.contract, order)
            logger.info(f"Modified TAKE PROFIT for {symbol}: {old_price} -> {take_profit_price}")

            # Cancel any extra TP orders
            for extra in tp_trades[1:]:
                try:
                    self.ib.cancelOrder(extra.order)
                except Exception as e:
                    logger.warning(f"Failed to cancel extra TP order for {symbol}: {e}")

            return True

        # Otherwise, create a new TP
        qty = int(quantity) if quantity is not None else int(self.get_position_quantity(symbol))
        if qty <= 0:
            logger.warning(f"Cannot create take-profit for {symbol}: no long position quantity available")
            return False

        tp_order = LimitOrder("SELL", qty, float(take_profit_price))
        tp_order.transmit = True

        if stop_trades:
            oca_group = f"OCA_EXIT_{symbol}"
            tp_order.ocaGroup = oca_group
            tp_order.ocaType = 1
            for extra in stop_trades[1:]:
                try:
                    self.ib.cancelOrder(extra.order)
                except Exception as e:
                    logger.warning(f"Failed to cancel extra stop order for {symbol}: {e}")
            sl_order = stop_trades[0].order
            sl_order.ocaGroup = oca_group
            sl_order.ocaType = 1
            self.ib.placeOrder(stop_trades[0].contract, sl_order)

        self.ib.placeOrder(contract, tp_order)
        logger.info(f"Placed TAKE PROFIT for {symbol}: {take_profit_price} (qty={qty})")
        return True

    def modify_stop_loss(self, symbol: str, new_stop_price: float) -> bool:
        """
        Modify an existing stop-loss order to a new price.
        Returns True if successfully modified, False otherwise.
        """
        trades = self.get_open_trades_for_symbol(symbol)
        
        for trade in trades:
            order = trade.order
            # Stop orders have auxPrice as the stop price
            if order.orderType == 'STP' and order.action == 'SELL':
                old_price = order.auxPrice
                order.auxPrice = float(new_stop_price)
                self.ib.placeOrder(trade.contract, order)
                logger.info(f"Modified STOP LOSS for {symbol}: {old_price} -> {new_stop_price}")
                return True
        
        logger.warning(f"No stop-loss order found for {symbol}")
        return False

    def modify_take_profit(self, symbol: str, new_take_profit_price: float) -> bool:
        """
        Modify an existing take-profit (limit) order to a new price.
        Returns True if successfully modified, False otherwise.
        """
        trades = self.get_open_trades_for_symbol(symbol)
        
        for trade in trades:
            order = trade.order
            # Limit orders have lmtPrice as the limit price
            if order.orderType == 'LMT' and order.action == 'SELL':
                old_price = order.lmtPrice
                order.lmtPrice = float(new_take_profit_price)
                self.ib.placeOrder(trade.contract, order)
                logger.info(f"Modified TAKE PROFIT for {symbol}: {old_price} -> {new_take_profit_price}")
                return True
        
        logger.warning(f"No take-profit order found for {symbol}")
        return False

    def get_orders_summary_for_symbol(self, symbol: str) -> dict:
        """
        Get a summary of open orders for a symbol.
        Returns dict with stop_loss and take_profit prices if found.
        """
        trades = self.get_open_trades_for_symbol(symbol)
        summary = {
            "stop_loss": None,
            "take_profit": None,
            "stop_order_id": None,
            "tp_order_id": None,
        }
        
        for trade in trades:
            order = trade.order
            if order.orderType == 'STP' and order.action == 'SELL':
                summary["stop_loss"] = order.auxPrice
                summary["stop_order_id"] = order.orderId
            elif order.orderType == 'LMT' and order.action == 'SELL':
                summary["take_profit"] = order.lmtPrice
                summary["tp_order_id"] = order.orderId
        
        return summary

    def get_pending_sell_orders_for_symbol(self, symbol: str) -> list:
        """
        Get all pending SELL orders for a symbol (MKT, LMT, or STP).
        Used to prevent duplicate sell orders.
        """
        trades = self.get_open_trades_for_symbol(symbol)
        pending_sells = []
        for trade in trades:
            order = trade.order
            if order.action == 'SELL':
                pending_sells.append({
                    "order_id": order.orderId,
                    "order_type": order.orderType,
                    "quantity": order.totalQuantity,
                    "status": trade.orderStatus.status,
                })
        return pending_sells

    def execute_buy_order(self, contract, quantity, stop_loss_price=None, take_profit_price=None):
        """
        Executes a buy order.

        If stop_loss_price and/or take_profit_price are provided, a bracket-style set of
        child orders is created so IBKR manages exits even if the bot disconnects.
        """
        if quantity <= 0:
            logger.warning(f"Invalid quantity {quantity} for {contract.symbol}")
            return None

        # Simple market order if no bracket exits requested
        if stop_loss_price is None and take_profit_price is None:
            parent_order = MarketOrder('BUY', quantity)
            trade = self.ib.placeOrder(contract, parent_order)
            logger.info(f"Placed BUY order for {quantity} shares of {contract.symbol}")
            return trade

        # Bracket-style without staging:
        # The traditional approach uses parent.transmit=False and relies on the last child transmit=True.
        # If a child order fails validation or is blocked by TWS precautionary settings, the parent can be
        # left untransmitted in TWS requiring a manual "Transmit" click.
        #
        # Instead, we transmit the parent immediately and then attach children using parentId.
        # This prevents the "Transmit" button scenario whilst still avoiding accidental shorts
        # (children remain inactive until the parent is filled).
        parent_id = int(self.ib.client.getReqId())
        parent_order = MarketOrder("BUY", quantity)
        parent_order.orderId = parent_id
        parent_order.transmit = True

        oca_group = f"OCA_{parent_id}"
        children = []

        if take_profit_price is not None:
            tp_order = LimitOrder("SELL", quantity, float(take_profit_price))
            tp_order.orderId = int(self.ib.client.getReqId())
            tp_order.parentId = parent_id
            tp_order.ocaGroup = oca_group
            tp_order.ocaType = 1
            tp_order.transmit = True
            children.append(tp_order)

        if stop_loss_price is not None:
            sl_order = StopOrder("SELL", quantity, float(stop_loss_price))
            sl_order.orderId = int(self.ib.client.getReqId())
            sl_order.parentId = parent_id
            sl_order.ocaGroup = oca_group
            sl_order.ocaType = 1
            sl_order.transmit = True
            children.append(sl_order)

        trade = self.ib.placeOrder(contract, parent_order)
        logger.info(f"Placed BUY (parent) for {quantity} shares of {contract.symbol} (orderId={parent_id})")

        # Attach exits as quickly as possible.
        for child in children:
            self.ib.placeOrder(contract, child)

        if take_profit_price is not None:
            logger.info(f"Placed TAKE PROFIT at {take_profit_price} for {contract.symbol}")
        if stop_loss_price is not None:
            logger.info(f"Placed STOP LOSS at {stop_loss_price} for {contract.symbol}")

        return trade

    def get_open_positions(self):
        """Returns a list of currently open positions."""
        return self.ib.positions()

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a specific order by ID. Returns True if cancellation was initiated."""
        trades = self._req_all_open_trades()
        for trade in trades:
            if trade.order.orderId == order_id:
                try:
                    self.ib.cancelOrder(trade.order)
                    logger.info(f"Cancelled order {order_id}")
                    return True
                except Exception as e:
                    logger.warning(f"Failed to cancel order {order_id}: {e}")
                    return False
        logger.warning(f"Order {order_id} not found in open trades")
        return False

    def adjust_order_price(self, order_id: int, new_price: float) -> bool:
        """
        Adjust the price of an existing order.
        For limit orders, adjusts lmtPrice.
        For stop orders, adjusts auxPrice.
        Returns True if modification was initiated.
        """
        trades = self._req_all_open_trades()
        for trade in trades:
            if trade.order.orderId == order_id:
                order = trade.order
                old_price = None
                
                if order.orderType == 'LMT':
                    old_price = order.lmtPrice
                    order.lmtPrice = float(new_price)
                elif order.orderType == 'STP':
                    old_price = order.auxPrice
                    order.auxPrice = float(new_price)
                else:
                    logger.warning(f"Cannot adjust price for order type {order.orderType}")
                    return False
                
                try:
                    self.ib.placeOrder(trade.contract, order)
                    logger.info(f"Adjusted order {order_id} price: {old_price} -> {new_price}")
                    return True
                except Exception as e:
                    logger.warning(f"Failed to adjust order {order_id}: {e}")
                    return False
        
        logger.warning(f"Order {order_id} not found in open trades")
        return False

    def get_all_open_orders_with_details(self) -> list[dict]:
        """
        Get all open orders with detailed information for AI review.
        Returns a list of dicts with order details and current market data.
        """
        result = []
        trades = self._req_all_open_trades()
        
        for trade in trades:
            order = trade.order
            contract = trade.contract
            status = trade.orderStatus
            
            # Get order price based on type
            order_price = None
            if order.orderType == 'LMT':
                order_price = order.lmtPrice
            elif order.orderType == 'STP':
                order_price = order.auxPrice
            
            # Calculate order age (approximate from log entries)
            age_minutes = 0
            if trade.log:
                first_entry = trade.log[0]
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                age_seconds = (now - first_entry.time).total_seconds()
                age_minutes = int(age_seconds / 60)
            
            result.append({
                "order_id": order.orderId,
                "symbol": contract.symbol,
                "exchange": contract.exchange,
                "currency": contract.currency,
                "contract": contract,  # Keep for execution
                "action": order.action,  # BUY or SELL
                "order_type": order.orderType,  # LMT, STP, MKT
                "quantity": int(order.totalQuantity),
                "order_price": order_price,
                "status": status.status,
                "filled": int(status.filled),
                "remaining": int(status.remaining),
                "age_minutes": age_minutes,
                "parent_id": order.parentId,  # 0 if standalone, or parent order ID
            })
        
        return result

