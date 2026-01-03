from ib_insync import ScannerSubscription, TagValue
import logging

logger = logging.getLogger(__name__)

# Trading classes to exclude (microcap/small cap that trigger Rule 144 issues)
EXCLUDED_TRADING_CLASSES = {"SCM"}  # Small Cap Market

# Default scan codes used when not configured in YAML/runtime config.
DEFAULT_SCAN_CODES = ["MOST_ACTIVE", "TOP_PERC_GAIN", "HOT_BY_VOLUME", "HIGH_VS_13W_HI"]

# Friendly names for common IBKR scan codes (purely for logging/UI visibility).
SCAN_CODE_LABELS = {
    "MOST_ACTIVE": "Most Active",
    "TOP_PERC_GAIN": "Top Gainers",
    "HOT_BY_VOLUME": "High Volume",
    "HIGH_VS_13W_HI": "Near 13-Week High",
}


class MarketScreener:
    def __init__(self, ib_connection, config):
        self.ib = ib_connection.ib
        self.config = config

    def get_dynamic_candidates(self):
        """
        Uses multiple IBKR Scanners to find low-cost, high-volatility candidates 
        in both US and UK markets.
        
        Filters out microcap stocks (SCM trading class) to avoid Rule 144 compliance issues.
        """
        candidates = []

        markets = [str(m).upper() for m in (self.config.get("trading", {}).get("markets") or [])]
        if not markets:
            logger.error("No markets configured (trading.markets is empty); skipping scanner cycle.")
            return []
        
        trading_cfg = self.config.get("trading", {})
        screener_cfg = trading_cfg.get("screener", {}) if isinstance(trading_cfg.get("screener", {}), dict) else {}
        max_price = float(trading_cfg.get("max_share_price", 20.0))
        min_price = float(trading_cfg.get("min_share_price", 2.0))  # Avoid very cheap stocks
        min_avg_volume = int(trading_cfg.get("min_avg_volume", 500000))  # Minimum average volume
        exclude_microcap = bool(trading_cfg.get("exclude_microcap", True))  # Exclude SCM stocks

        # Universe configuration (what the AI will analyse).
        max_candidates = int(screener_cfg.get("max_candidates", 250))
        if max_candidates <= 0:
            max_candidates = 250

        scan_codes_raw = screener_cfg.get("scan_codes", None)
        if scan_codes_raw is None:
            scan_codes = list(DEFAULT_SCAN_CODES)
        elif isinstance(scan_codes_raw, list):
            scan_codes = [str(x).strip().upper() for x in scan_codes_raw if isinstance(x, str) and x.strip()]
        else:
            scan_codes = list(DEFAULT_SCAN_CODES)

        include_raw = screener_cfg.get("include_symbols", []) if isinstance(screener_cfg.get("include_symbols", []), list) else []
        exclude_raw = screener_cfg.get("exclude_symbols", []) if isinstance(screener_cfg.get("exclude_symbols", []), list) else []
        exclude_set = {str(x).strip().upper().split(",", 1)[0] for x in exclude_raw if isinstance(x, str) and x.strip()}
        
        def _resolve_market(entry: str) -> tuple[str, str] | None:
            """
            Parse an include_symbols entry.
            Accepted forms:
            - "SYMBOL"
            - "SYMBOL,US" / "SYMBOL,UK"
            Returns (symbol, market) or None if invalid.
            """
            raw = str(entry or "").strip()
            if not raw:
                return None
            if "," in raw:
                sym, mk = [p.strip() for p in raw.split(",", 1)]
                sym_u = sym.upper()
                mk_u = mk.upper()
                if sym_u and mk_u in {"US", "UK"}:
                    return (sym_u, mk_u)
                return None

            sym_u = raw.upper()
            if not sym_u:
                return None
            # If only one market is configured, use it; otherwise prefer US when available.
            if len(markets) == 1:
                return (sym_u, markets[0])
            return (sym_u, "US" if "US" in markets else markets[0])

        # Start with manual inclusions (if any), then add scanner results.
        for entry in include_raw:
            if not isinstance(entry, str) or not entry.strip():
                continue
            resolved = _resolve_market(entry)
            if resolved is None:
                continue
            sym_u, mk_u = resolved
            if sym_u in exclude_set:
                continue
            if mk_u not in markets:
                logger.warning("include_symbols entry %r ignored (market %s not enabled in trading.markets)", entry, mk_u)
                continue
            if mk_u == "US":
                candidates.append(
                    {
                        "symbol": sym_u,
                        "exchange": "SMART",
                        "currency": "USD",
                        "scan_source": "Manual",
                        "trading_class": "",
                    }
                )
            else:
                candidates.append(
                    {
                        "symbol": sym_u,
                        "exchange": "LSE",
                        "currency": "GBP",
                        "scan_source": "Manual",
                        "trading_class": "",
                    }
                )

        # Define scan types to broaden the search (configurable via trading.screener.scan_codes)
        scan_configs = [{"code": c, "desc": SCAN_CODE_LABELS.get(c, c)} for c in scan_codes]

        # Filter by price and volume using TagValues
        tag_values = [
            TagValue('priceBelow', str(max_price)),
            TagValue('priceAbove', str(min_price)),
            TagValue('volumeAbove', str(min_avg_volume)),  # Filter low-volume stocks
        ]

        # 1. Scan US Market
        if "US" in markets and scan_configs:
            for scan in scan_configs:
                logger.info(f"Scanning US market ({scan['desc']}) ${min_price}-${max_price}, vol>{min_avg_volume}...")
                us_subscription = ScannerSubscription(
                    instrument='STK',
                    locationCode='STK.US.MAJOR',
                    scanCode=scan['code']
                )
                try:
                    results = self.ib.reqScannerData(us_subscription, scannerSubscriptionFilterOptions=tag_values)
                    for res in results:
                        contract = res.contractDetails.contract
                        trading_class = getattr(contract, 'tradingClass', '') or ''
                        if str(contract.symbol).upper() in exclude_set:
                            continue
                        
                        # Skip microcap stocks (SCM = Small Cap Market)
                        if exclude_microcap and trading_class in EXCLUDED_TRADING_CLASSES:
                            logger.debug(f"Skipping {contract.symbol} (trading class: {trading_class})")
                            continue
                        
                        candidates.append({
                            'symbol': contract.symbol,
                            'exchange': 'SMART',
                            'currency': 'USD',
                            'scan_source': scan['desc'],
                            'trading_class': trading_class,
                        })
                except Exception as e:
                    logger.error(f"US Scan {scan['code']} failed: {e}")

        # 2. Scan UK Market (LSE)
        if "UK" in markets and scan_configs:
            for scan in scan_configs:
                logger.info(f"Scanning UK market ({scan['desc']}) £{min_price}-£{max_price}...")
                uk_subscription = ScannerSubscription(
                    instrument='STK',
                    locationCode='STK.LSE',
                    scanCode=scan['code']
                )
                try:
                    results = self.ib.reqScannerData(uk_subscription, scannerSubscriptionFilterOptions=tag_values)
                    for res in results:
                        contract = res.contractDetails.contract
                        if str(contract.symbol).upper() in exclude_set:
                            continue
                        candidates.append({
                            'symbol': contract.symbol,
                            'exchange': 'LSE',
                            'currency': 'GBP',
                            'scan_source': scan['desc'],
                            'trading_class': getattr(contract, 'tradingClass', '') or '',
                        })
                except Exception as e:
                    logger.debug(f"UK Scan {scan['code']} failed (often restricted): {e}")

        # Remove duplicates and limit to a reasonable number for AI processing
        unique_candidates = []
        seen = set()
        for c in candidates:
            if c['symbol'] not in seen:
                unique_candidates.append(c)
                seen.add(c['symbol'])
        
        logger.info(f"Dynamic screening found {len(unique_candidates)} unique candidates across all scans.")
        return unique_candidates[:max_candidates]  # Limit candidates for AI processing

