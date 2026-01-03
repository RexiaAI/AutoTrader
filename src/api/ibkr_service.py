from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from ib_insync import IB

from src.domain.models import AccountSummaryItem, OpenOrderRow, PositionRow

logger = logging.getLogger(__name__)


class IBKRService:
    """
    Thread-safe IBKR live data service for FastAPI.

    - Runs a single persistent IB connection in its own dedicated thread + event loop.
    - All IB calls are executed on that loop (ib_insync is not thread-safe).
    - Endpoints can await results without blocking the FastAPI event loop.

    This avoids per-request connections ("connection spam") and avoids asyncio loop conflicts
    with uvicorn/uvloop.
    """

    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        *,
        connect_timeout: float = 10.0,
        request_timeout: float = 8.0,
        reconnect_cooldown_seconds: float = 10.0,
        open_orders_ttl_seconds: float = 2.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.connect_timeout = float(connect_timeout)
        self.request_timeout = float(request_timeout)
        self.reconnect_cooldown_seconds = float(reconnect_cooldown_seconds)
        self.open_orders_ttl_seconds = float(open_orders_ttl_seconds)

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ib: IB | None = None

        self._ready_evt = threading.Event()
        self._stop_evt = threading.Event()
        self._connected_evt = threading.Event()

        self._last_connect_attempt = 0.0
        self._account: str | None = None

        # Simple TTL cache to prevent UI-driven spamming of expensive IBKR calls.
        # These are only accessed on the IB loop thread.
        self._open_orders_cache: list[OpenOrderRow] | None = None
        self._open_orders_cache_ts: float = 0.0
        self._open_orders_inflight: asyncio.Task[list[OpenOrderRow]] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._thread_main, name="ibkr-loop", daemon=True)
        self._thread.start()
        # Wait briefly for loop/ib to be ready; do not block startup indefinitely.
        self._ready_evt.wait(timeout=5.0)

    def stop(self) -> None:
        self._stop_evt.set()
        if self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            except Exception:
                pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5.0)

    def is_ready(self) -> bool:
        return self._ready_evt.is_set()

    def is_connected(self) -> bool:
        return self._connected_evt.is_set() and bool(self._ib and self._ib.isConnected())

    async def get_account_summary(self) -> list[AccountSummaryItem]:
        return await self._run_on_ib_loop(self._get_account_summary(), timeout=self.request_timeout)

    async def get_positions(self) -> list[PositionRow]:
        return await self._run_on_ib_loop(self._get_positions(), timeout=self.request_timeout)

    async def get_open_orders(self) -> list[OpenOrderRow]:
        return await self._run_on_ib_loop(self._get_open_orders(), timeout=self.request_timeout)

    # -------------------
    # Internal
    # -------------------

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ib = IB()

        # Shorter timeout for all requests; prevents hanging calls.
        self._ib.RequestTimeout = float(self.request_timeout)

        self._ready_evt.set()

        # Start connection manager task
        loop.create_task(self._connection_manager())

        try:
            loop.run_forever()
        finally:
            try:
                if self._ib and self._ib.isConnected():
                    self._ib.disconnect()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass

    async def _shutdown(self) -> None:
        self._stop_evt.set()
        if self._ib and self._ib.isConnected():
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._connected_evt.clear()

    async def _connection_manager(self) -> None:
        assert self._ib is not None

        while not self._stop_evt.is_set():
            if not self._ib.isConnected():
                now = time.time()
                if now - self._last_connect_attempt >= self.reconnect_cooldown_seconds:
                    self._last_connect_attempt = now
                    await self._connect_once()
            await asyncio.sleep(1.0)

    async def _connect_once(self) -> None:
        assert self._ib is not None

        self._connected_evt.clear()
        try:
            logger.info(
                "IBKRService connecting to %s:%s (clientId=%s, timeout=%ss)",
                self.host,
                self.port,
                self.client_id,
                self.connect_timeout,
            )
            await self._ib.connectAsync(
                self.host,
                self.port,
                clientId=self.client_id,
                timeout=self.connect_timeout,
                readonly=False,
            )

            # Prefer delayed market data if not subscribed.
            try:
                self._ib.reqMarketDataType(3)
            except Exception:
                pass

            self._connected_evt.set()
            logger.info("IBKRService connected")

            # Subscribe to account updates so portfolio/accountValues are populated.
            # Do this AFTER we mark the service connected; the first update can take a few seconds.
            try:
                accounts = self._ib.managedAccounts()
                self._account = accounts[0] if accounts else None
                if self._account:
                    async def _subscribe() -> None:
                        await self._ib.reqAccountUpdatesAsync(self._account)  # populates portfolio/accountValues

                    task = asyncio.create_task(_subscribe())

                    def _log_task_result(t: asyncio.Task) -> None:
                        try:
                            t.result()
                        except Exception as ex:
                            logger.warning("IBKRService account updates subscription failed: %s", ex)

                    task.add_done_callback(_log_task_result)
            except Exception as e:
                logger.warning("IBKRService account updates subscription setup failed: %s", e)
        except Exception as e:
            logger.error("IBKRService connect failed: %s", e)
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._connected_evt.clear()

    async def _run_on_ib_loop(self, coro, *, timeout: float) -> Any:
        if not self._loop:
            raise RuntimeError("IBKR service not initialised")

        # Schedule on the IB loop thread and await from FastAPI loop without blocking.
        cfut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        wrapped = asyncio.wrap_future(cfut)
        return await asyncio.wait_for(wrapped, timeout=float(timeout))

    async def _require_connected(self) -> IB:
        if not self._ib:
            raise RuntimeError("IBKR not initialised")
        if not self._ib.isConnected():
            raise RuntimeError("IBKR not connected")
        return self._ib

    async def _get_account_summary(self) -> list[AccountSummaryItem]:
        ib = await self._require_connected()

        important_tags = {
            "TotalCashValue",
            "CashBalance",
            "NetLiquidation",
            "GrossPositionValue",
            "AvailableFunds",
            "UnrealizedPnL",
            "RealizedPnL",
        }

        out_by_key: dict[tuple[str, str], AccountSummaryItem] = {}

        def _add(tag: str, currency: str, value: float) -> None:
            out_by_key[(tag, currency)] = AccountSummaryItem(tag=tag, value=float(value), currency=currency)

        # IMPORTANT: Do NOT spam reqAccountSummary/reqAccountSummaryAsync; IBKR will rate-limit
        # and throw Error 322 ("Maximum number of account summary requests exceeded").
        # We rely on the account values cache populated by reqAccountUpdatesAsync subscription.
        for av in ib.accountValues():
            if av.tag not in important_tags:
                continue
            try:
                value = float(av.value)
            except (ValueError, TypeError):
                continue
            _add(av.tag, av.currency, value)

        return list(out_by_key.values())

    async def _get_positions(self) -> list[PositionRow]:
        ib = await self._require_connected()

        # Prefer portfolio items (includes market price/value and PnL) if we have them.
        portfolio = [p for p in ib.portfolio() if getattr(p, "position", 0)]
        if portfolio:
            positions: list[PositionRow] = []
            for item in portfolio:
                try:
                    positions.append(
                        PositionRow(
                            symbol=item.contract.symbol,
                            exchange=item.contract.exchange or "",
                            currency=item.contract.currency,
                            position=int(item.position),
                            avg_cost=float(item.averageCost),
                            market_price=float(item.marketPrice),
                            market_value=float(item.marketValue),
                            unrealised_pnl=float(item.unrealizedPNL),
                            realised_pnl=float(item.realizedPNL),
                        )
                    )
                except Exception:
                    continue
            return positions

        # Otherwise, build a snapshot from positions + tickers (still live IBKR data).
        raw_positions = await ib.reqPositionsAsync()
        live_positions = [p for p in raw_positions if float(getattr(p, "position", 0) or 0) != 0]
        if not live_positions:
            return []

        contracts = [p.contract for p in live_positions]
        tickers = await ib.reqTickersAsync(*contracts)
        ticker_by_conid = {getattr(t.contract, "conId", None): t for t in tickers}

        out: list[PositionRow] = []
        for p in live_positions:
            conid = getattr(p.contract, "conId", None)
            t = ticker_by_conid.get(conid)
            qty = float(p.position)
            avg_cost = float(p.avgCost)
            market_price = None
            if t is not None:
                try:
                    mp = t.marketPrice()
                except Exception:
                    mp = None
                if mp is None or mp != mp:
                    mp = t.last or t.close
                if mp is not None and mp == mp:
                    market_price = float(mp)

            market_value = float(market_price * qty) if market_price is not None else None
            unrealised_pnl = float((market_price - avg_cost) * qty) if market_price is not None else None

            out.append(
                PositionRow(
                    symbol=p.contract.symbol,
                    exchange=p.contract.exchange or "",
                    currency=p.contract.currency,
                    position=int(qty),
                    avg_cost=avg_cost,
                    market_price=market_price,
                    market_value=market_value,
                    unrealised_pnl=unrealised_pnl,
                    realised_pnl=None,
                )
            )
        return out

    async def _get_open_orders(self) -> list[OpenOrderRow]:
        ib = await self._require_connected()

        # Backpressure: return cached results if fresh, and de-duplicate concurrent refreshes.
        now = time.monotonic()
        if self._open_orders_cache is not None and (now - self._open_orders_cache_ts) < self.open_orders_ttl_seconds:
            return list(self._open_orders_cache)

        if self._open_orders_inflight is not None and not self._open_orders_inflight.done():
            try:
                return await self._open_orders_inflight
            except Exception:
                # If an inflight task failed, fall through and refresh.
                pass

        async def _refresh() -> list[OpenOrderRow]:
            # IMPORTANT:
            # - In multi-client setups, `reqAllOpenOrders*` may not return trades directly even though
            #   the IB connection is receiving `openOrder` updates (which populate `ib.trades()`).
            # - `ib.openTrades()` is built from that internal state and is the most reliable way to
            #   get the currently known open orders for this connection.
            try:
                await ib.reqAllOpenOrdersAsync()
            except Exception:
                # Some TWS/Gateway setups restrict `reqAllOpenOrders`; still return what we already know.
                pass

            # Give the IB loop a moment to process any `openOrder` callbacks.
            await asyncio.sleep(0.05)

            trades_local = list(ib.openTrades())
            orders_local: list[OpenOrderRow] = []
            for trade in trades_local:
                try:
                    order = trade.order
                    contract = trade.contract
                    status = trade.orderStatus
                    orders_local.append(
                        OpenOrderRow(
                            order_id=int(order.orderId),
                            symbol=contract.symbol,
                            exchange=contract.exchange or "",
                            currency=contract.currency,
                            action=order.action,
                            order_type=order.orderType,
                            total_qty=float(order.totalQuantity),
                            filled=float(status.filled),
                            remaining=float(status.remaining),
                            status=status.status,
                            lmt_price=float(getattr(order, "lmtPrice", 0) or 0),
                            aux_price=float(getattr(order, "auxPrice", 0) or 0),
                        )
                    )
                except Exception:
                    continue
            return orders_local

        self._open_orders_inflight = asyncio.create_task(_refresh())
        orders: list[OpenOrderRow] = []
        try:
            orders = await self._open_orders_inflight
            self._open_orders_cache = list(orders)
            self._open_orders_cache_ts = time.monotonic()
            return list(orders)
        finally:
            # Clear inflight if finished (success or failure).
            if self._open_orders_inflight is not None and self._open_orders_inflight.done():
                self._open_orders_inflight = None


