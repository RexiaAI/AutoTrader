import pytest

try:
    import ib_insync  # noqa: F401
except Exception:
    pytest.skip("ib_insync is not installed; skipping IBKR integration tests.", allow_module_level=True)

import os
import pandas as pd
from src.broker.connection import IBConnection
from src.data.retrieval import MarketData
from src.research.analyser import ResearchAnalyser
from src.research.ai_researcher import AIResearcher
from src.research.screener import MarketScreener
from src.trading.executor import TradeExecutor
from src.utils.database import (
    init_db,
    log_to_db,
    log_event,
    record_trade,
    get_trades,
    get_recent_logs,
    get_live_status,
    get_events,
)

# Load config
from src.utils.config_loader import load_config

pytestmark = pytest.mark.integration

# Ensure we don't accidentally disable IBKR in integration runs.
os.environ.pop("AUTOTRADER_DISABLE_IBKR_SERVICE", None)

config = load_config()


def _require_openai_key() -> None:
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        pytest.skip("OPENAI_API_KEY not set; skipping OpenAI integration tests.")

@pytest.fixture(scope="module")
def ib_conn():
    conn = IBConnection()
    # Use a test-specific client id so tests can run even if the bot is running.
    conn.client_id = 999
    connected = conn.connect()
    if not connected:
        pytest.fail("Could not connect to IBKR. Ensure TWS/Gateway is running in Paper Trading mode on port 7497.")
    yield conn
    conn.disconnect()

@pytest.mark.order(1)
def test_broker_connection(ib_conn):
    """Test 1: Confirm connection to Interactive Brokers."""
    assert ib_conn.ib.isConnected()
    assert ib_conn.is_paper_trading()

@pytest.mark.order(2)
def test_database_functionality():
    """Test 2: Confirm database operations."""
    init_db()
    log_to_db("TEST", "Integration test log entry")
    log_event("TEST", "Integration test event", symbol="TEST_SYM", step="TEST_STEP")
    record_trade("TEST_SYM", "BUY", 10, 1.50, 1.20, None, 0.5, "TESTING")
    
    logs = get_recent_logs(limit=1)
    trades = get_trades()
    live = get_live_status()
    events = get_events(limit=5)
    
    assert not logs.empty
    assert "Integration test log entry" in logs.iloc[0]['message']
    assert not trades.empty
    assert trades.iloc[0]['symbol'] == "TEST_SYM"
    assert live is not None
    assert live["id"] == 1
    assert not events.empty

@pytest.mark.order(3)
def test_market_data_us(ib_conn):
    """Test 3: Confirm fetching US market data (AAPL)."""
    data_engine = MarketData(ib_conn)
    df = data_engine.fetch_historical_data("AAPL", exchange="SMART", currency="USD", duration="10 D")
    assert not df.empty
    assert "close" in df.columns
    assert len(df) > 0

@pytest.mark.order(4)
def test_market_data_uk(ib_conn):
    """Test 4: Confirm fetching UK market data (BARC)."""
    data_engine = MarketData(ib_conn)
    df = data_engine.fetch_historical_data("BARC", exchange="LSE", currency="GBP", duration="10 D")
    assert not df.empty
    assert "close" in df.columns
    assert len(df) > 0

@pytest.mark.order(5)
def test_ai_researcher_sentiment():
    """Test 5: Confirm GPT-4.1-mini sentiment analysis."""
    _require_openai_key()
    ai = AIResearcher(model=config['ai']['model'])
    headlines = [
        "Company reports record profits and explosive growth",
        "New product launch exceeds all expectations"
    ]
    score = ai.analyse_news_sentiment("TEST", headlines)
    assert isinstance(score, float)
    assert -1.0 <= score <= 1.0

@pytest.mark.order(6)
def test_research_analyser_logic(ib_conn):
    """Test 6: Confirm technical indicators and screening logic."""
    data_engine = MarketData(ib_conn)
    analyser = ResearchAnalyser(config)
    
    # Get some data to test on
    df = data_engine.fetch_historical_data("AAPL", duration="60 D")
    df_with_indicators = analyser.apply_technical_indicators(df)
    
    assert "RSI_14" in df_with_indicators.columns
    assert "ATRr_14" in df_with_indicators.columns
    assert "volatility_ratio" in df_with_indicators.columns

@pytest.mark.order(7)
def test_trading_executor_account(ib_conn):
    """Test 7: Confirm access to account summary and risk calculations."""
    executor = TradeExecutor(ib_conn, config)
    acc_summary = ib_conn.ib.accountSummary()
    assert len(acc_summary) > 0
    
    # Verify we can find NetLiquidation
    equity = float([v.value for v in acc_summary if v.tag == 'NetLiquidation'][0])
    assert equity > 0
    
    # Test position size calculation
    size = executor.calculate_position_size(100.0, 95.0)
    assert isinstance(size, int)
    assert size >= 0

@pytest.mark.order(8)
def test_end_to_end_screening_run(ib_conn):
    """Test 8: Confirm the full pipeline for a single stock."""
    data_engine = MarketData(ib_conn)
    analyser = ResearchAnalyser(config)
    
    # Test LLoyds (UK Stock, typically low cost)
    symbol, exch, curr = "LLOY", "LSE", "GBP"
    df = data_engine.fetch_historical_data(symbol, exch, curr, duration="30 D")
    df = analyser.apply_technical_indicators(df)
    
    # We don't assert true/false for should_trade as it depends on market conditions,
    # but we assert the method runs without error.
    should_trade = analyser.screen_stock(df)
    assert isinstance(should_trade, bool)

@pytest.mark.order(9)
def test_market_screener(ib_conn):
    """Test 9: Confirm the dynamic screener finds candidates."""
    screener = MarketScreener(ib_conn, config)
    candidates = screener.get_dynamic_candidates()
    
    # Note: On weekends or during maintenance, this might be empty,
    # but we want to ensure the call succeeds.
    assert isinstance(candidates, list)
    if candidates:
        assert 'symbol' in candidates[0]
        assert 'exchange' in candidates[0]


@pytest.mark.order(10)
def test_ai_trade_decision_json():
    """Test 10: Confirm GPT-4.1-mini can return a structured intraday trade decision JSON."""
    _require_openai_key()
    ai = AIResearcher(model=config["ai"]["model"])

    intraday_cfg = config.get("intraday", {}) or {}
    intraday_ctx = {
        "enabled": bool(intraday_cfg.get("enabled", False)),
        "bar_size": str(intraday_cfg.get("bar_size", "5 mins")),
        "duration": str(intraday_cfg.get("duration", "2 D")),
        "use_rth": bool(intraday_cfg.get("use_rth", True)),
        "flatten_minutes_before_close": int(intraday_cfg.get("flatten_minutes_before_close", 10)),
        "stop_atr_multiplier": float(intraday_cfg.get("stop_atr_multiplier", 2.0)),
        "take_profit_r": float(intraday_cfg.get("take_profit_r", 1.0)),
    }

    out = ai.decide_intraday_trade(
        symbol="TEST",
        exchange="SMART",
        currency="USD",
        price=1.50,
        indicators={"rsi_14": 48.0, "atr": 0.12, "volatility_ratio": 0.08, "bb_mid": 1.40},
        headlines=[
            "Company reports record profits and explosive growth",
            "New product launch exceeds expectations, shares jump in pre-market trading",
        ],
        reddit=None,
        intraday=intraday_ctx,
    )

    assert isinstance(out, dict)
    assert out["decision"] in {"SHORTLIST", "SKIP"}
    assert 0.0 <= float(out["confidence"]) <= 1.0
    assert 0.0 <= float(out["score"]) <= 1.0
    assert -1.0 <= float(out["sentiment"]) <= 1.0
    assert isinstance(out["rationale"], str) and len(out["rationale"]) > 0
    assert isinstance(out["key_factors"], list)
    assert isinstance(out["key_risks"], list)


@pytest.mark.order(11)
def test_ai_trade_decision_without_headlines_with_reddit():
    """
    Test 11: If IBKR headlines are unavailable, we allow AI decision only when Reddit context exists.
    This test validates the AI method accepts empty headlines when reddit is provided.
    """
    _require_openai_key()
    ai = AIResearcher(model=config["ai"]["model"])

    intraday_cfg = config.get("intraday", {}) or {}
    intraday_ctx = {
        "enabled": bool(intraday_cfg.get("enabled", False)),
        "bar_size": str(intraday_cfg.get("bar_size", "5 mins")),
        "duration": str(intraday_cfg.get("duration", "2 D")),
        "use_rth": bool(intraday_cfg.get("use_rth", True)),
        "flatten_minutes_before_close": int(intraday_cfg.get("flatten_minutes_before_close", 10)),
        "stop_atr_multiplier": float(intraday_cfg.get("stop_atr_multiplier", 2.0)),
        "take_profit_r": float(intraday_cfg.get("take_profit_r", 1.0)),
    }

    out = ai.decide_intraday_trade(
        symbol="TEST",
        exchange="SMART",
        currency="USD",
        price=1.50,
        indicators={"rsi_14": 48.0, "atr": 0.12, "volatility_ratio": 0.08, "bb_mid": 1.40},
        headlines=[],
        reddit={"mentions": 25, "sentiment": 0.72, "confidence": 0.7, "strong_signal": True},
        intraday=intraday_ctx,
    )

    assert isinstance(out, dict)
    assert out["decision"] in {"SHORTLIST", "SKIP"}
    assert 0.0 <= float(out["confidence"]) <= 1.0
    assert 0.0 <= float(out["score"]) <= 1.0
    assert -1.0 <= float(out["sentiment"]) <= 1.0
    assert isinstance(out["rationale"], str) and len(out["rationale"]) > 0

