import logging
import json
import re

from ib_insync import Stock

from src.broker.connection import IBConnection
from src.data.news import IBKRNewsFetcher
from src.data.reddit import RedditCache, load_reddit_config
from src.data.retrieval import MarketData
from src.research.analyser import ResearchAnalyser
from src.research.ai_researcher import AIResearcher
from src.research.screener import EXCLUDED_TRADING_CLASSES, MarketScreener
from src.trading.executor import TradeExecutor
from src.trading.position_manager import PositionManager
from src.utils.database import (
    init_db,
    log_to_db,
    log_event,
    get_runtime_config,
    record_trade,
    record_account_summary,
    update_performance,
    log_research,
    update_research_decision,
    update_live_status,
    get_reddit_state,
    set_reddit_state,
    insert_reddit_sentiments,
    get_latest_reddit_sentiment,
    force_commit,  # Ensure batched writes are flushed
)
import time
import signal
from functools import partial

# Trader helpers (kept out of the main loop for readability/testability).
from src.trader.flatten import flatten_positions_if_needed
from src.trader.ibkr_account import get_account_value
from src.trader.market_hours import is_market_open, is_near_market_close
from src.trader.snapshots import snapshot_portfolio_and_orders
from src.trader.timeout import SymbolTimeout, process_with_timeout

# Prefer a shared config loader (single source of truth).
from src.utils.config_loader import load_config
from src.utils.runtime_config import apply_runtime_config, normalise_runtime_config, validate_runtime_config

# Timeout for processing a single symbol (seconds)
SYMBOL_TIMEOUT_SECONDS = 45

logger = logging.getLogger(__name__)

# Reddit ticker extraction (conservative: only $TICKER patterns).
_RE_REDDIT_TICKER = re.compile(r"\$([A-Z]{1,5})\b")


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for c in candidates:
        sym = str(c.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        out.append(c)
        seen.add(sym)
    return out


def main():
    # Configure logging (idempotent; safe if configured elsewhere).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Initialise DB schema / connections (idempotent).
    init_db()

    # Load configuration
    base_config = load_config()
    config = base_config

    # Initialize components
    conn = IBConnection(config=config)
    
    # Retry connection for up to 5 minutes (morning startup safety)
    connected = False
    start_time = time.time()
    while not connected and (time.time() - start_time) < 300:
        if conn.connect():
            connected = True
            break
        logger.warning("IBKR connection failed, retrying in 10 seconds...")
        time.sleep(10)

    if not connected:
        logger.error("Could not connect to IBKR after 5 minutes. Exiting.")
        return

    data_engine = MarketData(conn)
    analyser = ResearchAnalyser(config)
    ai_researcher = AIResearcher(model=config['ai']['model'], config=config)
    news_fetcher = IBKRNewsFetcher(conn)
    screener = MarketScreener(conn, config)
    executor = TradeExecutor(conn, config)
    
    # Position management (AI-driven sell/adjust decisions)
    position_manager = PositionManager(
        ib_connection=conn,
        executor=executor,
        data_engine=data_engine,
        ai_researcher=ai_researcher,
        config=config,
    )
    pm_cfg = config.get("position_management", {}) or {}
    position_review_interval = int(pm_cfg.get("review_interval_seconds", 60))
    last_position_review_time = 0.0
    
    max_positions = int(config["trading"]["max_positions"])
    max_new_positions_per_cycle = int(config["trading"]["max_new_positions_per_cycle"])
    max_cash_utilisation = float(config["trading"]["max_cash_utilisation"])
    cash_budget_tag = str(config["trading"].get("cash_budget_tag", "TotalCashValue"))
    intraday_cfg = config.get("intraday", {}) or {}
    intraday_enabled = bool(intraday_cfg.get("enabled", False))
    intraday_bar_size = str(intraday_cfg.get("bar_size", "5 mins"))
    intraday_duration = str(intraday_cfg.get("duration", "2 D"))
    intraday_use_rth = bool(intraday_cfg.get("use_rth", True))
    cycle_interval_seconds = int(intraday_cfg.get("cycle_interval_seconds", 3600))
    # When all configured markets are closed, we can slow down significantly to avoid needless scanning/AI calls.
    cycle_interval_seconds_closed = int(intraday_cfg.get("cycle_interval_seconds_closed", 1800))
    stop_atr_multiplier = float(intraday_cfg.get("stop_atr_multiplier", 2.0))
    take_profit_r = float(intraday_cfg.get("take_profit_r", 1.0))
    flatten_minutes_before_close = int(intraday_cfg.get("flatten_minutes_before_close", 10))

    reddit_cfg = load_reddit_config(config)
    reddit_cache = RedditCache(reddit_cfg) if reddit_cfg.enabled else None

    active_ai_model = str(config.get("ai", {}).get("model") or "")
    
    # Track top candidates from each cycle for position comparison
    last_top_candidates: list[dict] = []
    last_market_context: dict | None = None

    try:
        while True:
            # Apply runtime config overlay (DB-backed) at the start of each cycle.
            try:
                runtime_doc = get_runtime_config()
                runtime_n = normalise_runtime_config(runtime_doc)
                validate_runtime_config(runtime_n)
                config = apply_runtime_config(base_config, runtime_n)
            except Exception as e:
                msg = f"Runtime config unavailable/invalid: {type(e).__name__}: {str(e)[:200]}"
                logger.error(msg)
                log_event("ERROR", msg, symbol="Config", step="Runtime")
                update_live_status("Config", "Runtime config error; trading paused")
                force_commit()
                time.sleep(int(cycle_interval_seconds))
                continue

            # Push updated config into long-lived components.
            screener.config = config
            analyser.config = config
            executor.config = config

            # Update AI model if changed.
            next_model = str(config.get("ai", {}).get("model") or "")
            if next_model and next_model != active_ai_model:
                ai_researcher = AIResearcher(model=next_model, config=config)
                position_manager.ai_researcher = ai_researcher
                active_ai_model = next_model

            # Keep the AI researcher pointed at the latest effective config (prompt addenda, etc).
            ai_researcher.config = config

            # Refresh derived knobs used throughout the loop.
            pm_cfg = config.get("position_management", {}) or {}
            position_review_interval = int(pm_cfg.get("review_interval_seconds", 60))
            position_manager.config = config
            position_manager.enabled = True
            position_manager.review_interval_seconds = position_review_interval

            max_positions = int(config["trading"]["max_positions"])
            max_new_positions_per_cycle = int(config["trading"]["max_new_positions_per_cycle"])
            max_cash_utilisation = float(config["trading"]["max_cash_utilisation"])
            cash_budget_tag = str(config["trading"].get("cash_budget_tag", "TotalCashValue"))
            intraday_cfg = config.get("intraday", {}) or {}
            intraday_enabled = bool(intraday_cfg.get("enabled", False))
            intraday_bar_size = str(intraday_cfg.get("bar_size", "5 mins"))
            intraday_duration = str(intraday_cfg.get("duration", "2 D"))
            intraday_use_rth = bool(intraday_cfg.get("use_rth", True))
            cycle_interval_seconds = int(intraday_cfg.get("cycle_interval_seconds", 3600))
            cycle_interval_seconds_closed = int(intraday_cfg.get("cycle_interval_seconds_closed", 1800))
            stop_atr_multiplier = float(intraday_cfg.get("stop_atr_multiplier", 2.0))
            take_profit_r = float(intraday_cfg.get("take_profit_r", 1.0))
            flatten_minutes_before_close = int(intraday_cfg.get("flatten_minutes_before_close", 10))

            # SAFETY: Cancel orphaned SELL orders and close any accidental short positions.
            # This is a safeguard against shorts in a long-only strategy.
            try:
                orphaned = executor.cancel_orphaned_sell_orders()
                if orphaned:
                    log_event("WARN", f"Cancelled {orphaned} orphaned SELL order(s)", symbol="Safety", step="Shorts")
                
                closed_shorts = executor.close_all_shorts()
                for sym, qty in closed_shorts:
                    log_event("WARN", f"Closed short position: {qty} shares", symbol=sym, step="Shorts")
            except Exception as e:
                logger.warning(f"Safety check failed: {e}")

            # Risk settings for sizing.
            executor.risk_per_trade = float(config["trading"]["risk_per_trade"])

            # Feature toggles.
            reddit_cfg = load_reddit_config(config)
            if reddit_cfg.enabled:
                reddit_cache = RedditCache(reddit_cfg)
            else:
                reddit_cache = None

            cycle_start_time = time.time()
            logger.info("Starting analysis cycle...")
            log_to_db("INFO", "Starting analysis cycle")
            log_event("INFO", "Starting analysis cycle", symbol="Cycle", step="Start")
            update_live_status("Screener", "Starting market scan")

            if intraday_enabled:
                flatten_positions_if_needed(conn, flatten_minutes_before_close)
            
            # Position & order management: AI-driven reviews (HOLD/SELL/ADJUST and KEEP/CANCEL/ADJUST).
            now = time.time()
            if now - last_position_review_time >= position_review_interval:
                try:
                    # Review positions
                    review_results = position_manager.review_all_positions(
                        top_candidates=last_top_candidates,
                        market_context=last_market_context,
                    )
                    if review_results:
                        actions_taken = [r for r in review_results if r.get("executed")]
                        if actions_taken:
                            log_event("INFO", f"Position Manager: {len(actions_taken)} actions executed", symbol="PM", step="Summary")

                    # Review open orders (unfilled buys/sells)
                    # AI-driven: allow the AI to review immediately (no hard-coded minimum age gate)
                    order_review_results = position_manager.review_open_orders(
                        min_age_minutes=0,
                        market_context=last_market_context,
                    )
                    if order_review_results:
                        order_actions_taken = [r for r in order_review_results if r.get("executed")]
                        if order_actions_taken:
                            log_event(
                                "INFO",
                                f"Order Manager: {len(order_actions_taken)} orders adjusted/cancelled",
                                symbol="OM",
                                step="Summary",
                            )

                    last_position_review_time = now
                except Exception as e:
                    logger.error(f"Position/Order management failed: {e}")
                    log_event("ERROR", f"Position/Order management failed: {e}", symbol="PM", step="Error")

            # If all configured markets are closed, skip the heavy research cycle and sleep longer.
            if intraday_enabled:
                markets = [str(m).upper() for m in (config.get("trading", {}).get("markets") or [])]
                any_open = False
                if "UK" in markets:
                    any_open = any_open or is_market_open("LSE", "GBP")
                if "US" in markets:
                    any_open = any_open or is_market_open("SMART", "USD")

                if not any_open:
                    mins = max(1, int(cycle_interval_seconds_closed // 60))
                    msg = f"Market closed — next cycle in ~{mins} min"
                    logger.info(msg)
                    log_event("INFO", msg, symbol="Cycle", step="MarketClosed")
                    update_live_status("Idle", msg)
                    force_commit()
                    time.sleep(int(cycle_interval_seconds_closed))
                    continue
            
            # 0. Dynamic Screening (The AI Research phase)
            try:
                update_live_status("Screener", "Requesting IBKR scanner results")
                log_event("INFO", "Requesting IBKR scanner results", symbol="Screener", step="Scan")
                current_watch_list = screener.get_dynamic_candidates()
                if not current_watch_list:
                    msg = "Screener returned no candidates."
                    logger.error(msg)
                    log_to_db("ERROR", msg)
                    log_event("ERROR", msg, symbol="Screener", step="Scan")
                    update_live_status("Screener", "No candidates returned; attempting Reddit augmentation")
                else:
                    logger.info(f"Using {len(current_watch_list)} dynamically found candidates.")
                    log_to_db("INFO", f"Screener returned {len(current_watch_list)} candidates")
                    log_event("INFO", f"Screener returned {len(current_watch_list)} candidates", symbol="Screener", step="Scan")

            except Exception as e:
                logger.error(f"Screener failed: {e}")
                log_to_db("ERROR", f"Screener failed: {e}")
                log_event("ERROR", f"Screener failed: {e}", symbol="Screener", step="Scan")
                update_live_status("Screener", "Screener failed; waiting for next cycle")
                time.sleep(cycle_interval_seconds)
                continue

            # Reddit refresh (at most once per hour; cached locally)
            if reddit_cache is not None:
                try:
                    update_live_status("Reddit", "Refreshing cache (if due)")
                    refreshed = reddit_cache.refresh_posts_if_due()
                    log_event(
                        "INFO",
                        "Reddit cache refreshed" if refreshed else "Reddit cache still fresh (no fetch)",
                        symbol="Reddit",
                        step="Fetch",
                    )
                except Exception as e:
                    # Do not fabricate Reddit data; we simply log the failure and proceed without it.
                    log_event("ERROR", f"Reddit fetch failed: {e}", symbol="Reddit", step="Fetch")

            # Optional: include tickers mentioned on Reddit into the universe (before AI analysis).
            try:
                trading_cfg = config.get("trading", {}) or {}
                screener_cfg = trading_cfg.get("screener", {}) if isinstance(trading_cfg.get("screener", {}), dict) else {}
                include_reddit_symbols = bool(screener_cfg.get("include_reddit_symbols", False))
                max_candidates = int(screener_cfg.get("max_candidates", 250))
                if max_candidates <= 0:
                    max_candidates = 250

                exclude_raw = screener_cfg.get("exclude_symbols", []) if isinstance(screener_cfg.get("exclude_symbols", []), list) else []
                exclude_set = {str(x).strip().upper().split(",", 1)[0] for x in exclude_raw if isinstance(x, str) and x.strip()}
                markets = [str(m).upper() for m in (trading_cfg.get("markets") or [])]
                exclude_microcap = bool(trading_cfg.get("exclude_microcap", True))

                if include_reddit_symbols and reddit_cache is not None:
                    posts = reddit_cache.get_cached_posts(limit=500)
                    if posts:
                        # Extract $TICKER mentions (conservative to reduce false positives).
                        counts: dict[str, int] = {}
                        for p in posts:
                            text = f"{p.get('title','')} {p.get('selftext','')}".upper()
                            for sym in _RE_REDDIT_TICKER.findall(text):
                                if not sym:
                                    continue
                                counts[sym] = counts.get(sym, 0) + 1

                        existing = {str(c.get("symbol") or "").strip().upper() for c in (current_watch_list or [])}
                        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

                        reddit_candidates: list[dict] = []
                        for sym_u, _n in ranked:
                            if sym_u in existing or sym_u in exclude_set:
                                continue

                            cand: dict | None = None
                            if "US" in markets:
                                try:
                                    c = Stock(sym_u, "SMART", "USD")
                                    conn.ib.qualifyContracts(c)
                                    tc = getattr(c, "tradingClass", "") or ""
                                    if exclude_microcap and tc in EXCLUDED_TRADING_CLASSES:
                                        continue
                                    cand = {
                                        "symbol": sym_u,
                                        "exchange": "SMART",
                                        "currency": "USD",
                                        "scan_source": "Reddit",
                                        "trading_class": tc,
                                    }
                                except Exception:
                                    cand = None

                            if cand is None and "UK" in markets:
                                try:
                                    c = Stock(sym_u, "LSE", "GBP")
                                    conn.ib.qualifyContracts(c)
                                    tc = getattr(c, "tradingClass", "") or ""
                                    if exclude_microcap and tc in EXCLUDED_TRADING_CLASSES:
                                        continue
                                    cand = {
                                        "symbol": sym_u,
                                        "exchange": "LSE",
                                        "currency": "GBP",
                                        "scan_source": "Reddit",
                                        "trading_class": tc,
                                    }
                                except Exception:
                                    cand = None

                            if cand is not None:
                                reddit_candidates.append(cand)
                                # Cap extra work; overall list is still capped below.
                                if len(reddit_candidates) >= 50:
                                    break

                        if reddit_candidates:
                            # Ensure Reddit symbols are included even when the scanner hits the cap.
                            merged = _dedupe_candidates(reddit_candidates + (current_watch_list or []))
                            current_watch_list = merged[:max_candidates]
                            log_event(
                                "INFO",
                                f"Added {len(reddit_candidates)} Reddit symbol(s) into universe",
                                symbol="Reddit",
                                step="Universe",
                            )
            except Exception as e:
                log_event("ERROR", f"Reddit universe augmentation failed: {e}", symbol="Reddit", step="Universe")

            # If we still have no candidates after optional Reddit augmentation, skip the cycle.
            if not current_watch_list:
                msg = "No candidates available; waiting for next cycle."
                log_event("ERROR", msg, symbol="Screener", step="Scan")
                update_live_status("Screener", msg)
                time.sleep(cycle_interval_seconds)
                continue

            # Update performance metrics
            try:
                acc_summary = conn.ib.accountSummary()
                # Persist a small subset for the dashboard.
                # Include CashBalance so we can track specific currency availability in the UI.
                for tag in ["NetLiquidation", "TotalCashValue", "AvailableFunds", "GrossPositionValue", "CashBalance"]:
                    for v in [x for x in acc_summary if x.tag == tag]:
                        record_account_summary(v.tag, float(v.value), v.currency)

                # Prefer a deterministic currency selection for equity so the time series is stable.
                equity = get_account_value(acc_summary, "NetLiquidation", currency=None)
                if equity is None:
                    raise ValueError("NetLiquidation not available in account summary")
                
                # Fetch account-level P&L from portfolio (more reliable than reqPnL)
                total_unrealised = 0.0
                total_realised = 0.0
                try:
                    portfolio = conn.ib.portfolio()
                    for item in portfolio:
                        upnl = getattr(item, 'unrealizedPNL', None)
                        rpnl = getattr(item, 'realizedPNL', None)
                        if upnl is not None:
                            total_unrealised += float(upnl)
                        if rpnl is not None:
                            total_realised += float(rpnl)
                except Exception as pnl_err:
                    logger.warning(f"Failed to fetch PnL from portfolio: {pnl_err}")
                
                # Record to account_summary for the dashboard
                record_account_summary("UnrealizedPnL", total_unrealised, "USD")
                record_account_summary("RealizedPnL", total_realised, "USD")
                
                update_performance(equity, total_unrealised, total_realised) 
            except Exception as e:
                logger.error(f"Failed to update performance: {e}")
                log_event("ERROR", f"Failed to update account snapshot: {e}", symbol="IBKR", step="Account")
            
            # Fetch market context (SPY/QQQ performance) for AI decisions
            market_context = {}
            try:
                update_live_status("Market", "Fetching SPY/QQQ context")
                spy_contract = Stock("SPY", "SMART", "USD")
                qqq_contract = Stock("QQQ", "SMART", "USD")
                conn.ib.qualifyContracts(spy_contract, qqq_contract)
                
                conn.ib.reqMktData(spy_contract, "", False, False)
                conn.ib.reqMktData(qqq_contract, "", False, False)
                conn.ib.sleep(0.5)
                
                spy_ticker = conn.ib.ticker(spy_contract)
                qqq_ticker = conn.ib.ticker(qqq_contract)
                
                # SPY context
                spy_last = getattr(spy_ticker, "last", None) or getattr(spy_ticker, "close", None)
                spy_prev = getattr(spy_ticker, "close", None)
                spy_change_pct = None
                if spy_last and spy_prev and spy_prev > 0:
                    spy_change_pct = round(((spy_last - spy_prev) / spy_prev) * 100, 2)
                
                # QQQ context  
                qqq_last = getattr(qqq_ticker, "last", None) or getattr(qqq_ticker, "close", None)
                qqq_prev = getattr(qqq_ticker, "close", None)
                qqq_change_pct = None
                if qqq_last and qqq_prev and qqq_prev > 0:
                    qqq_change_pct = round(((qqq_last - qqq_prev) / qqq_prev) * 100, 2)
                
                market_context = {
                    "spy_price": spy_last,
                    "spy_change_pct": spy_change_pct,
                    "qqq_price": qqq_last,
                    "qqq_change_pct": qqq_change_pct,
                    "market_sentiment": "bullish" if (spy_change_pct or 0) > 0.3 else ("bearish" if (spy_change_pct or 0) < -0.3 else "neutral"),
                }
                
                conn.ib.cancelMktData(spy_contract)
                conn.ib.cancelMktData(qqq_contract)
                
                log_event("INFO", f"Market: SPY {spy_change_pct or 'N/A'}%, QQQ {qqq_change_pct or 'N/A'}%", symbol="Market", step="Context")
            except Exception as e:
                logger.debug(f"Failed to fetch market context: {e}")
            
            try:
                positions = executor.get_open_positions()
            except Exception as e:
                logger.error(f"Failed to retrieve open positions: {e}")
                log_to_db("ERROR", f"Failed to retrieve open positions: {e}")
                log_event("ERROR", f"Failed to retrieve open positions: {e}", symbol="IBKR", step="Positions")
                update_live_status("IBKR", "Failed to retrieve open positions; waiting for next cycle")
                time.sleep(cycle_interval_seconds)
                continue

            open_symbols = {p.contract.symbol for p in positions}
            open_count = len(open_symbols)

            # Snapshot current positions + open orders for the dashboard.
            snapshot_portfolio_and_orders(conn)

            available_slots = max_positions - open_count
            max_new = max(0, min(available_slots, max_new_positions_per_cycle))
            if max_new == 0:
                msg = f"No capacity for new positions ({open_count}/{max_positions})."
                log_to_db("INFO", msg)
                log_event("INFO", msg, symbol="Risk", step="Limits")
                update_live_status("Idle", "At position limit; waiting for next cycle")
                time.sleep(cycle_interval_seconds)
                continue

            # Cash budgets by currency (no fallback: if we can't read a currency's AvailableFunds, we won't trade it)
            currencies = sorted({c["currency"] for c in current_watch_list if "currency" in c})
            budgets = {}
            min_cash_reserve = config.get("trading", {}).get("min_cash_reserve_by_currency") or {}
            for cur in currencies:
                available = get_account_value(acc_summary, cash_budget_tag, currency=cur)
                if available is None:
                    budgets[cur] = 0.0
                    log_event(
                        "ERROR",
                        f"{cash_budget_tag} not available for {cur}; budget set to 0.",
                        symbol="Risk",
                        step="Cash",
                    )
                    continue
                reserve = 0.0
                try:
                    if isinstance(min_cash_reserve, dict):
                        reserve = float(min_cash_reserve.get(cur, 0.0) or 0.0)
                except Exception:
                    reserve = 0.0

                cap_by_util = float(available) * max_cash_utilisation
                cap_by_reserve = float(available) - reserve
                budgets[cur] = max(0.0, min(cap_by_util, cap_by_reserve))
                log_event(
                    "INFO",
                    f"Budget for {cur}: {budgets[cur]:.2f} (from {cash_budget_tag}={available:.2f}, reserve={reserve:.2f}, util={max_cash_utilisation:.2f})",
                    symbol="Risk",
                    step="Cash",
                )
            budget_remaining = dict(budgets)
            net_liq = get_account_value(acc_summary, "NetLiquidation", currency=None)

            # Reddit sentiment cache (latest computed), used for optional override + scoring.
            reddit_map: dict[str, dict] = {}
            if reddit_cfg.enabled:
                try:
                    rs = get_latest_reddit_sentiment()
                    if not rs.empty:
                        for _, row in rs.iterrows():
                            sym = str(row.get("symbol") or "").upper()
                            if not sym:
                                continue
                            reddit_map[sym] = {
                                "mentions": int(row.get("mentions") or 0),
                                "sentiment": float(row.get("sentiment")),
                                "confidence": float(row.get("confidence")),
                            }
                except Exception as e:
                    log_event("ERROR", f"Failed to load Reddit sentiment cache: {e}", symbol="Reddit", step="Cache")

            # Run Reddit AI sentiment analysis at most once per hour, based on cached posts.
            if reddit_cache is not None:
                try:
                    state = get_reddit_state()
                    now = int(time.time())
                    last_analysis = int(state.get("last_analysis_utc") or 0)
                    if now - last_analysis >= reddit_cfg.analysis_interval_seconds:
                        # Mark analysis attempt time up-front to avoid repeated retries within the interval.
                        set_reddit_state(last_analysis_utc=now)
                        update_live_status("Reddit", "Analysing Reddit sentiment (hourly)")
                        posts = reddit_cache.get_cached_posts(limit=500)
                        if posts:
                            symbols = sorted({c["symbol"] for c in current_watch_list if "symbol" in c})
                            symbol_to_snippets: dict[str, list[str]] = {}
                            symbol_mentions: dict[str, int] = {}

                            # Build per-symbol snippets from cached posts (no extra Reddit calls).
                            for sym in symbols:
                                sym_u = sym.upper()
                                hits: list[str] = []
                                for p in posts:
                                    text = f"{p.get('title','')} {p.get('selftext','')}".upper()
                                    # crude boundary match
                                    if f" {sym_u} " in f" {text} ":
                                        snippet = f"[r/{p.get('subreddit')}] {p.get('title','')}"
                                        hits.append(snippet[:280])
                                if hits:
                                    symbol_to_snippets[sym_u] = hits[: reddit_cfg.max_posts_per_symbol]
                                    symbol_mentions[sym_u] = len(hits)

                            if symbol_to_snippets:
                                log_event("INFO", f"Running Reddit sentiment AI for {len(symbol_to_snippets)} symbols", symbol="Reddit", step="AI")
                                ai_out = ai_researcher.analyse_reddit_sentiment(symbol_to_snippets)
                                rows = []
                                for sym_u, res in ai_out.items():
                                    if sym_u not in symbol_mentions:
                                        continue
                                    rows.append(
                                        {
                                            "symbol": sym_u,
                                            "mentions": int(symbol_mentions[sym_u]),
                                            "sentiment": float(res["sentiment"]),
                                            "confidence": float(res["confidence"]),
                                            "rationale": str(res.get("rationale") or ""),
                                            "source_fetch_utc": int(state.get("last_fetch_utc") or 0),
                                        }
                                    )

                                if rows:
                                    insert_reddit_sentiments(rows)
                                    log_event("INFO", f"Reddit sentiment updated for {len(rows)} symbols", symbol="Reddit", step="AI")
                                    # Refresh map for this cycle
                                    reddit_map = {r["symbol"]: r for r in rows}
                        else:
                            log_event("INFO", "No cached Reddit posts to analyse", symbol="Reddit", step="AI")
                except Exception as e:
                    log_event("ERROR", f"Reddit sentiment analysis failed: {e}", symbol="Reddit", step="AI")

            eligible = []  # candidates eligible for ranking/selection
            total = len(current_watch_list)
            for idx, item in enumerate(current_watch_list, start=1):
                symbol = item["symbol"]
                exchange = item["exchange"]
                currency = item["currency"]

                log_to_db("INFO", f"Researching {symbol} ({idx}/{total})")
                update_live_status(symbol, f"Initiating analysis ({idx}/{total})")

                # Defaults (we always write a research_log row, even on failure)
                price = None
                rsi = None
                vol_ratio = None
                sentiment_score = None
                ai_reasoning = ""
                score = None
                decision = "REJECTED"
                decision_reason = "Uninitialised"
                reddit_mentions = None
                reddit_sentiment = None
                reddit_confidence = None
                reddit_override = 0
                eligible_candidate = None

                try:
                    update_live_status(symbol, f"Fetching market data ({idx}/{total})")
                    log_event("INFO", "Fetching market data", symbol=symbol, step="Market Data")
                    if intraday_enabled:
                        df = data_engine.fetch_historical_data(
                            symbol,
                            exchange,
                            currency,
                            duration=intraday_duration,
                            bar_size=intraday_bar_size,
                            use_rth=intraday_use_rth,
                        )
                    else:
                        df = data_engine.fetch_historical_data(symbol, exchange, currency)
                    if df.empty:
                        decision = "REJECTED"
                        decision_reason = "No market data"
                    else:
                        update_live_status(symbol, f"Calculating technical indicators ({idx}/{total})")
                        log_event("INFO", "Calculating technical indicators", symbol=symbol, step="Indicators")
                        df = analyser.apply_technical_indicators(df)
                        latest = df.iloc[-1]
                        
                        # Momentum analysis from recent bars
                        bar_momentum = {}
                        try:
                            recent_bars = df.tail(10)
                            if len(recent_bars) >= 5:
                                # Price momentum (are closes trending up or down?)
                                closes = recent_bars["close"].values
                                momentum_5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0
                                momentum_10 = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0
                                
                                # Volume acceleration (is volume increasing?)
                                volumes = recent_bars["volume"].values
                                recent_avg_vol = volumes[-3:].mean() if len(volumes) >= 3 else 0
                                older_avg_vol = volumes[:3].mean() if len(volumes) >= 3 else 1
                                volume_acceleration = recent_avg_vol / older_avg_vol if older_avg_vol > 0 else 1
                                
                                # Bar direction (how many of last 5 bars are green?)
                                opens = recent_bars["open"].values[-5:]
                                closes_5 = closes[-5:]
                                green_bars = sum(1 for o, c in zip(opens, closes_5) if c > o)
                                
                                bar_momentum = {
                                    "momentum_5_bars_pct": round(momentum_5, 2),
                                    "momentum_10_bars_pct": round(momentum_10, 2),
                                    "volume_acceleration": round(volume_acceleration, 2),
                                    "green_bars_last_5": green_bars,
                                    "trend": "bullish" if momentum_5 > 0.5 and green_bars >= 3 else ("bearish" if momentum_5 < -0.5 and green_bars <= 2 else "neutral"),
                                }
                        except Exception:
                            pass

                        price = float(latest.get("close"))
                        rsi = float(latest.get("RSI_14")) if latest.get("RSI_14") is not None else None
                        vol_ratio = (
                            float(latest.get("volatility_ratio"))
                            if latest.get("volatility_ratio") is not None
                            else None
                        )
                        # Attach cached Reddit metrics (if available)
                        r = reddit_map.get(symbol.upper())
                        if r:
                            reddit_mentions = int(r.get("mentions") or 0)
                            reddit_sentiment = float(r.get("sentiment"))
                            reddit_confidence = float(r.get("confidence"))
                        # Helpful flag only (not used to decide): "strong Reddit signal" according to config.
                        if (
                            reddit_cfg.enabled
                            and reddit_cfg.override_enabled
                            and reddit_mentions is not None
                            and reddit_sentiment is not None
                            and reddit_confidence is not None
                            and reddit_mentions >= reddit_cfg.override_min_mentions
                            and reddit_sentiment >= reddit_cfg.override_sentiment_threshold
                            and reddit_confidence >= reddit_cfg.override_min_confidence
                        ):
                            reddit_override = 1

                        # Prepare a qualified contract for news + potential execution.
                        contract = data_engine.get_contract(symbol, exchange, currency)

                        # Fetch comprehensive market context for AI decision
                        fundamental_ctx = {}
                        try:
                            # Request real-time ticker snapshot
                            conn.ib.reqMktData(contract, "", False, False)
                            conn.ib.sleep(1.0)  # Allow time for data to arrive
                            ticker = conn.ib.ticker(contract)
                            
                            # Core volume/liquidity metrics
                            volume = getattr(ticker, "volume", None)
                            avg_volume = getattr(ticker, "avVolume", None)
                            relative_volume = None
                            if volume and avg_volume and avg_volume > 0:
                                relative_volume = round(volume / avg_volume, 2)
                            
                            # Intraday price action
                            high = getattr(ticker, "high", None)
                            low = getattr(ticker, "low", None)
                            open_price = getattr(ticker, "open", None)
                            last = getattr(ticker, "last", None) or price
                            prev_close = getattr(ticker, "close", None)
                            
                            # Gap analysis (how much did it gap from previous close?)
                            gap_pct = None
                            if open_price and prev_close and prev_close > 0:
                                gap_pct = round(((open_price - prev_close) / prev_close) * 100, 2)
                            
                            # Intraday range position (where is price within today's range?)
                            range_position = None
                            if high and low and high > low and last:
                                range_position = round((last - low) / (high - low), 2)
                            
                            # Bid/Ask spread (liquidity indicator)
                            bid = getattr(ticker, "bid", None)
                            ask = getattr(ticker, "ask", None)
                            spread_pct = None
                            if bid and ask and bid > 0:
                                spread_pct = round(((ask - bid) / bid) * 100, 3)
                            
                            # 52-week high/low context
                            high_52w = getattr(ticker, "high52", None)
                            low_52w = getattr(ticker, "low52", None)
                            pct_from_52w_high = None
                            pct_from_52w_low = None
                            if last and high_52w and high_52w > 0:
                                pct_from_52w_high = round(((last - high_52w) / high_52w) * 100, 2)
                            if last and low_52w and low_52w > 0:
                                pct_from_52w_low = round(((last - low_52w) / low_52w) * 100, 2)
                            
                            fundamental_ctx = {
                                "market_cap": getattr(ticker, "marketCap", None),
                                "volume": volume,
                                "avg_volume": avg_volume,
                                "relative_volume": relative_volume,
                                "prev_close": prev_close,
                                "open": open_price,
                                "high": high,
                                "low": low,
                                "last": last,
                                "gap_pct": gap_pct,
                                "range_position": range_position,
                                "bid": bid,
                                "ask": ask,
                                "spread_pct": spread_pct,
                                "high_52w": high_52w,
                                "low_52w": low_52w,
                                "pct_from_52w_high": pct_from_52w_high,
                                "pct_from_52w_low": pct_from_52w_low,
                            }
                            
                            # Cancel market data subscription to free up slots
                            conn.ib.cancelMktData(contract)
                        except Exception as e:
                            logger.debug(f"Failed to fetch ticker snapshot for {symbol}: {e}")

                        headlines: list[str] = []
                        # Fetch real headlines (no fabricated data).
                        update_live_status(symbol, f"Fetching IBKR news ({idx}/{total})")
                        log_event("INFO", "Fetching IBKR news", symbol=symbol, step="News")
                        try:
                            headlines = news_fetcher.fetch_headlines(contract, lookback_days=7, limit=10)
                        except Exception as e:
                            headlines = []
                            log_event("WARN", f"IBKR headlines unavailable: {e}", symbol=symbol, step="News")

                        update_live_status(symbol, f"AI shortlist ({config['ai']['model']}) ({idx}/{total})")
                        log_event("INFO", f"AI shortlist ({config['ai']['model']})", symbol=symbol, step="AI")

                        # Provide the AI with the raw signals (not hard-coded pass/fail thresholds).
                        bb_mid = None
                        bb_mid_cols = [col for col in latest.index if str(col).startswith("BBM_")]
                        if bb_mid_cols and latest.get(bb_mid_cols[0]) is not None:
                            bb_mid = float(latest.get(bb_mid_cols[0]))

                        atr_val = None
                        atr_cols = [col for col in latest.index if str(col).startswith("ATRr_")]
                        if atr_cols and latest.get(atr_cols[0]) is not None:
                            atr_val = float(latest.get(atr_cols[0]))

                        indicators = {
                            "rsi_14": rsi,
                            "atr": atr_val,
                            "volatility_ratio": vol_ratio,
                            "bb_mid": bb_mid,
                        }
                        reddit_ctx = (
                            {
                                "mentions": reddit_mentions,
                                "sentiment": reddit_sentiment,
                                "confidence": reddit_confidence,
                                "strong_signal": bool(reddit_override),
                            }
                            if reddit_mentions is not None and reddit_sentiment is not None and reddit_confidence is not None
                            else None
                        )

                        intraday_ctx = {
                            "enabled": intraday_enabled,
                            "bar_size": intraday_bar_size,
                            "duration": intraday_duration,
                            "use_rth": intraday_use_rth,
                            "flatten_minutes_before_close": flatten_minutes_before_close,
                            "stop_atr_multiplier": stop_atr_multiplier,
                            "take_profit_r": take_profit_r,
                        }

                        # Determine data sources available for AI decision
                        data_sources = []
                        if headlines:
                            data_sources.append("News")
                        if reddit_ctx:
                            data_sources.append("Reddit")
                        if fundamental_ctx and fundamental_ctx.get("volume"):
                            data_sources.append("Fundamentals")
                        data_sources.append("Technicals")  # Always have this

                        ai_label = f"AI ({'+'.join(data_sources)})"
                        log_event(
                            "INFO",
                            f"AI shortlist using: {', '.join(data_sources)}",
                            symbol=symbol,
                            step="AI",
                        )

                        ai_decision = ai_researcher.decide_intraday_trade(
                            symbol=symbol,
                            exchange=exchange,
                            currency=currency,
                            price=price,
                            indicators=indicators,
                            headlines=headlines,
                            reddit=reddit_ctx,
                            intraday=intraday_ctx,
                            fundamentals=fundamental_ctx,
                            bar_momentum=bar_momentum,
                            market_context=market_context,
                        )

                        sentiment_score = float(ai_decision["sentiment"])
                        score = float(ai_decision["score"])
                        ai_reasoning = json.dumps(ai_decision, ensure_ascii=False, indent=2)

                        if ai_decision["decision"] == "SHORTLIST":
                            decision = "SHORTLISTED"
                            decision_reason = f"{ai_label}: SHORTLIST (conf {ai_decision['confidence']:.2f}) — {ai_decision['rationale']}"
                        else:
                            decision = "REJECTED"
                            decision_reason = f"{ai_label}: SKIP (conf {ai_decision['confidence']:.2f}) — {ai_decision['rationale']}"

                        # Trading gates that can be decided immediately (market hours, already holding, budget).
                        # Only shortlisted candidates proceed to final selection.
                        if decision == "SHORTLISTED":
                            # Check hard blockers that prevent any trade
                            if not is_market_open(exchange, currency):
                                decision = "REJECTED"
                                decision_reason = f"Market closed: {decision_reason}"
                            elif intraday_enabled and is_near_market_close(exchange, currency, flatten_minutes_before_close):
                                decision = "REJECTED"
                                decision_reason = "Too close to market close (no new entries)"
                            elif symbol in open_symbols:
                                decision = "REJECTED"
                                decision_reason = "Already holding a position"
                            elif budget_remaining.get(currency, 0.0) <= 0.0:
                                decision = "REJECTED"
                                decision_reason = f"No available cash budget for {currency}"
                            else:
                                # Add to shortlist for comparison
                                final_score = float(score) if score else 0.0
                                
                                eligible_candidate = {
                                    "symbol": symbol,
                                    "exchange": exchange,
                                    "currency": currency,
                                    "price": price,
                                    "contract": contract,
                                    "latest": latest,
                                    "score": final_score,
                                    "sentiment_score": sentiment_score,
                                    "ai": ai_decision,
                                    "reddit_mentions": reddit_mentions,
                                    "reddit_sentiment": reddit_sentiment,
                                    "reddit_confidence": reddit_confidence,
                                    "reddit_override": reddit_override,
                                    "decision_reason": decision_reason,
                                }

                except SymbolTimeout as e:
                    decision = "REJECTED"
                    decision_reason = f"Symbol processing timed out: {e}"
                    log_event("WARN", f"Symbol timed out after {SYMBOL_TIMEOUT_SECONDS}s", symbol=symbol, step="Timeout")
                except TimeoutError as e:
                    decision = "REJECTED"
                    decision_reason = f"Timeout during analysis: {e}"
                    log_event("WARN", f"Timeout: {e}", symbol=symbol, step="Timeout")
                except ConnectionError as e:
                    decision = "REJECTED"
                    decision_reason = f"Connection error: {e}"
                    log_event("ERROR", f"Connection error: {e}", symbol=symbol, step="Error")
                except Exception as e:
                    decision = "REJECTED"
                    decision_reason = f"Error during analysis: {type(e).__name__}: {e}"
                    log_event("ERROR", f"Unexpected error: {type(e).__name__}: {e}", symbol=symbol, step="Error")

                # Always record research results so the dashboard can explain the cycle.
                research_id = log_research(
                    symbol,
                    exchange,
                    currency,
                    price,
                    rsi,
                    vol_ratio,
                    sentiment_score,
                    ai_reasoning,
                    score,
                    None,
                    reddit_mentions,
                    reddit_sentiment,
                    reddit_confidence,
                    reddit_override,
                    decision,
                    decision_reason,
                )
                log_to_db("INFO", f"{symbol} decision: {decision} ({decision_reason})")
                log_event("INFO", f"Decision: {decision} ({decision_reason})", symbol=symbol, step="Decision")

                # Store the research_id so we can update it later for selected/unselected eligible candidates
                if eligible_candidate is not None:
                    eligible_candidate["research_id"] = research_id
                    eligible.append(eligible_candidate)

            # Selection + execution (Stage 2: compare shortlisted candidates and choose buys)
            if eligible:
                update_live_status("Selector", f"Comparing {len(eligible)} shortlisted candidates")
                log_event("INFO", f"Comparing {len(eligible)} shortlisted candidates", symbol="Selector", step="Rank")

                eligible.sort(key=lambda x: (x.get("score") is not None, x.get("score", 0.0)), reverse=True)

                # Persist the shortlist ranking (for visibility in the dashboard).
                for rank, cand in enumerate(eligible, start=1):
                    cand["rank"] = rank
                    update_research_decision(
                        cand["research_id"],
                        decision="SHORTLISTED",
                        reason=str(cand.get("decision_reason") or "Shortlisted"),
                        rank=rank,
                    )

                # If we have no capacity for new positions, stop here.
                if max_new <= 0:
                    log_event("INFO", "No capacity for new positions this cycle", symbol="Selector", step="Rank")
                    last_top_candidates = [
                        {
                            "symbol": c["symbol"],
                            "score": c.get("score"),
                            "rationale": c.get("decision_reason", ""),
                            "sentiment": c.get("sentiment_score"),
                        }
                        for c in eligible[:10]
                    ]
                else:
                    # AI selects which of the shortlisted candidates to BUY this cycle.
                    try:
                        candidates_for_ai = [
                            {
                                "symbol": c.get("symbol"),
                                "exchange": c.get("exchange"),
                                "currency": c.get("currency"),
                                "price": c.get("price"),
                                "rank": c.get("rank"),
                                "score": c.get("score"),
                                "ai": c.get("ai"),
                                "notes": c.get("decision_reason"),
                            }
                            for c in eligible
                        ]
                        sel = ai_researcher.select_buys_from_shortlist(
                            candidates=candidates_for_ai,
                            max_new=int(max_new),
                            budget_remaining=budget_remaining,
                            market_context=market_context,
                        )
                        desired = list(sel.get("selected_symbols") or [])
                        sel_rationale = str(sel.get("rationale") or "").strip()
                        if sel_rationale:
                            log_event("INFO", f"Buy selection: {sel_rationale}", symbol="Selector", step="AI")
                    except Exception as e:
                        msg = f"Buy selection AI failed: {type(e).__name__}: {str(e)[:200]}"
                        logger.error(msg)
                        log_event("ERROR", msg, symbol="Selector", step="AI")
                        update_live_status("Selector", "AI selection error; waiting for next cycle")
                        force_commit()
                        time.sleep(int(cycle_interval_seconds))
                        continue

                    desired_set = set(desired)
                    for cand in eligible:
                        if cand.get("symbol") not in desired_set:
                            update_research_decision(
                                cand["research_id"],
                                decision="SHORTLISTED",
                                reason="Shortlisted; not selected this cycle",
                                rank=cand["rank"],
                            )

                    cand_by_symbol = {str(c.get("symbol") or "").upper(): c for c in eligible}

                    selected: list[str] = []
                    for sym in desired:
                        if len(selected) >= max_new:
                            break
                        cand = cand_by_symbol.get(str(sym).upper())
                        if not cand:
                            continue

                        cur = cand["currency"]
                        price = float(cand["price"])
                        latest = cand["latest"]

                        atr = latest.get("ATRr_14")
                        if atr is None:
                            update_research_decision(
                                cand["research_id"],
                                decision="SHORTLISTED",
                                reason="Selected by AI but ATR missing; cannot set stop-loss",
                                rank=cand["rank"],
                            )
                            continue

                        stop_loss = price - (stop_atr_multiplier * float(atr))
                        if stop_loss <= 0:
                            update_research_decision(
                                cand["research_id"],
                                decision="SHORTLISTED",
                                reason="Selected by AI but stop-loss would be <= 0; skipping",
                                rank=cand["rank"],
                            )
                            continue

                        if net_liq is None:
                            update_research_decision(
                                cand["research_id"],
                                decision="SHORTLISTED",
                                reason="Selected by AI but net liquidation not available; cannot size position",
                                rank=cand["rank"],
                            )
                            continue

                        qty_risk = executor.calculate_position_size(price, stop_loss, net_liquidation=net_liq)
                        max_affordable = int(budget_remaining.get(cur, 0.0) // price)
                        quantity = int(min(qty_risk, max_affordable))
                        if quantity <= 0:
                            update_research_decision(
                                cand["research_id"],
                                decision="SHORTLISTED",
                                reason=f"Selected by AI but insufficient {cur} budget for position sizing",
                                rank=cand["rank"],
                            )
                            continue

                        update_live_status(cand["symbol"], f"Placing order (rank {cand['rank']})")
                        buy_rationale = str(cand.get("decision_reason") or "").strip()
                        if buy_rationale:
                            log_event(
                                "INFO",
                                f"AI selected for BUY (rank {cand['rank']}) — {buy_rationale}",
                                symbol=cand["symbol"],
                                step="Trade",
                            )
                        else:
                            log_event("INFO", f"Placing order (rank {cand['rank']})", symbol=cand["symbol"], step="Trade")

                        take_profit = price + (take_profit_r * (price - stop_loss))
                        trade = executor.execute_buy_order(
                            cand["contract"],
                            quantity,
                            stop_loss_price=stop_loss,
                            take_profit_price=take_profit,
                        )
                        if not trade:
                            update_research_decision(
                                cand["research_id"],
                                decision="SHORTLISTED",
                                reason="Selected by AI but order placement failed",
                                rank=cand["rank"],
                            )
                            continue

                        order_status = getattr(trade.orderStatus, "status", "Submitted")
                        record_trade(
                            cand["symbol"],
                            "BUY",
                            quantity,
                            price,
                            stop_loss,
                            take_profit,
                            float(cand.get("sentiment_score") or 0.0) if cand.get("sentiment_score") is not None else 0.0,
                            status=str(order_status),
                            rationale=buy_rationale or None,
                        )
                        budget_remaining[cur] = max(0.0, budget_remaining.get(cur, 0.0) - (quantity * price))
                        selected.append(cand["symbol"])

                        update_research_decision(
                            cand["research_id"],
                            decision="TRADE",
                            reason=f"Order placed ({order_status})",
                            rank=cand["rank"],
                        )
                        log_to_db("INFO", f"Placed BUY for {quantity} shares of {cand['symbol']} (status: {order_status})")

                    log_event(
                        "INFO",
                        f"Selected {len(selected)} trades: {', '.join(selected) if selected else 'None'}",
                        symbol="Selector",
                        step="Result",
                    )

                    # Update top candidates for position management (opportunity comparison)
                    last_top_candidates = [
                        {
                            "symbol": c["symbol"],
                            "score": c.get("score"),
                            "rationale": c.get("decision_reason", ""),
                            "sentiment": c.get("sentiment_score"),
                        }
                        for c in eligible[:10]  # Keep top 10
                    ]
            else:
                last_top_candidates = []
            
            # Update market context for position management
            last_market_context = market_context if market_context else None

            # Calculate cycle duration and smart sleep
            cycle_duration = time.time() - cycle_start_time
            cycle_duration_mins = cycle_duration / 60
            
            if cycle_duration >= cycle_interval_seconds:
                # Cycle took longer than interval - skip sleep and start next immediately
                overrun_secs = cycle_duration - cycle_interval_seconds
                msg = f"Cycle took {cycle_duration_mins:.1f}min (overran by {overrun_secs:.0f}s) — starting next immediately"
                logger.warning(msg)
                log_event("WARN", msg, symbol="Cycle", step="Timing")
                update_live_status("Cycle", f"Overran ({cycle_duration_mins:.1f}min) — restarting")
            else:
                # Sleep for remaining time until next interval
                remaining = cycle_interval_seconds - cycle_duration
                remaining_mins = remaining / 60
                msg = f"Cycle complete in {cycle_duration_mins:.1f}min. Next in {remaining_mins:.1f}min"
                logger.info(msg)
                log_event("INFO", msg, symbol="Cycle", step="Complete")
                update_live_status("Idle", f"Next cycle in {remaining_mins:.1f}min")
                force_commit()  # Flush all batched writes before sleeping
                time.sleep(remaining)

    except KeyboardInterrupt:
        logger.info("Stopping AutoTrader...")
    finally:
        conn.disconnect()

if __name__ == "__main__":
    main()

