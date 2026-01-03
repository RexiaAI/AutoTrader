import pandas as pd
import pandas_ta as ta
import logging

logger = logging.getLogger(__name__)

class ResearchAnalyser:
    def __init__(self, config):
        self.config = config

    def apply_technical_indicators(self, df):
        """Applies technical indicators to the dataframe."""
        if df.empty:
            return df
            
        # Ensure we have enough data for indicators
        if len(df) < 30:
            logger.warning("Not enough data to apply technical indicators.")
            return df

        # Apply indicators from config
        for indicator in self.config['research']['technical_indicators']:
            name = indicator['name']
            params = indicator.get('params', {})
            
            if name == "RSI":
                df.ta.rsi(length=params.get('window', 14), append=True)
            elif name == "ATR":
                df.ta.atr(length=params.get('window', 14), append=True)
            elif name == "BollingerBands":
                df.ta.bbands(length=params.get('window', 20), std=params.get('window_dev', 2), append=True)
        
        # Custom Volatility Metric: ATR / Close
        # Note: pandas-ta uses 'ATRr_length' as the column name
        atr_col = f"ATRr_{self.config['research']['technical_indicators'][1]['params']['window']}"
        df['volatility_ratio'] = df[atr_col] / df['close']
        
        return df

    def apply_indicators(self, df):
        """Alias for apply_technical_indicators for consistency."""
        return self.apply_technical_indicators(df)

    def calculate_bar_momentum(self, df):
        """Calculates momentum and volume acceleration from recent bars."""
        if df.empty or len(df) < 5:
            return {}
            
        try:
            recent_bars = df.tail(10)
            # Price momentum (are closes trending up or down?)
            closes = recent_bars["close"].values
            momentum_5 = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0
            momentum_10 = (closes[-1] - closes[0]) / closes[0] * 100 if len(closes) >= 10 and closes[0] > 0 else 0
            
            # Volume acceleration (is volume increasing?)
            volumes = recent_bars["volume"].values
            recent_avg_vol = volumes[-3:].mean() if len(volumes) >= 3 else 0
            older_avg_vol = volumes[:3].mean() if len(volumes) >= 3 else 1
            volume_acceleration = recent_avg_vol / older_avg_vol if older_avg_vol > 0 else 1
            
            # Bar direction (how many of last 5 bars are green?)
            opens = recent_bars["open"].values[-5:]
            closes_5 = closes[-5:]
            green_bars = sum(1 for o, c in zip(opens, closes_5) if c > o)
            
            return {
                "momentum_5_bars_pct": round(momentum_5, 2),
                "momentum_10_bars_pct": round(momentum_10, 2),
                "volume_acceleration": round(volume_acceleration, 2),
                "green_bars_last_5": green_bars,
                "trend": "bullish" if momentum_5 > 0.5 and green_bars >= 3 else ("bearish" if momentum_5 < -0.5 and green_bars <= 2 else "neutral"),
            }
        except Exception as e:
            logger.warning(f"Error calculating bar momentum: {e}")
            return {}

    def screen_stock(self, df):
        """Backwards-compatible boolean screener."""
        passed, _reason = self.screen_stock_with_reason(df)
        return passed

    def screen_stock_with_reason(self, df):
        """
        Screens a stock based on volatility and cost criteria, returning (passed, reason).
        This supports the dashboard explaining *why* a stock was accepted/rejected.
        """
        if df.empty:
            return False, "No market data"

        latest = df.iloc[-1]

        # Read thresholds dynamically so runtime config changes apply without re-instantiating.
        max_share_price = float(self.config["trading"]["max_share_price"])
        volatility_threshold = float(self.config["trading"]["volatility_threshold"])

        price = float(latest.get("close", 0.0))
        if price > max_share_price:
            return False, f"Price above max ({price:.2f} > {max_share_price:.2f})"

        vol_ratio = latest.get("volatility_ratio")
        if vol_ratio is None:
            return False, "Volatility ratio missing (indicators not computed)"
        vol_ratio = float(vol_ratio)
        if vol_ratio < volatility_threshold:
            return False, f"Volatility below threshold ({vol_ratio:.4f} < {volatility_threshold:.4f})"

        rsi = latest.get("RSI_14")
        if rsi is None:
            return False, "RSI missing (indicators not computed)"
        rsi = float(rsi)
        if rsi >= 70:
            return False, f"RSI too high ({rsi:.2f} >= 70)"

        # Bollinger Bands middle column (pandas-ta naming varies with length/std).
        bb_mid_cols = [col for col in latest.index if str(col).startswith("BBM_")]
        if not bb_mid_cols:
            return False, "Bollinger Bands missing"

        bb_mid = float(latest[bb_mid_cols[0]])
        if price <= bb_mid:
            return False, f"Close not above BB mid ({price:.2f} <= {bb_mid:.2f})"

        logger.info(f"Stock passed screening. Price: {price}, Volatility: {vol_ratio:.2%}")
        return True, "Passed technical criteria"

    def analyse_sentiment(self, symbol, news_headlines):
        """
        Sentiment analysis is handled by `src.research.ai_researcher.AIResearcher`.
        This method is intentionally not implemented to avoid returning fabricated values.
        """
        raise NotImplementedError("Use AIResearcher for sentiment analysis (no placeholder values).")

