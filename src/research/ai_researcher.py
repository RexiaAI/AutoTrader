import os
import json
import logging
from openai import OpenAI
from openai import APITimeoutError, APIConnectionError, RateLimitError
from src.research.prompts import (
    build_buy_selection_system_prompt,
    build_order_review_system_prompt,
    build_position_review_system_prompt,
    build_shortlist_system_prompt,
)

logger = logging.getLogger(__name__)

# Constants for API resilience
OPENAI_TIMEOUT_SECONDS = 30
OPENAI_MAX_RETRIES = 2

class AIResearcher:
    def __init__(self, model="gpt-4.1-mini", *, config: dict | None = None):
        """
        AI client wrapper.

        Provider selection is OpenAI-compatible:
        - OpenAI: set OPENAI_API_KEY (and optionally OPENAI_BASE_URL)
        - Ollama / other compatible servers: set OPENAI_BASE_URL and (optionally) OPENAI_API_KEY
          (a dummy key is accepted by many local providers).
        """
        base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or None
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()

        # Many OpenAI-compatible local providers (e.g. Ollama) accept any key, but the SDK expects one.
        if not api_key and base_url:
            api_key = "ollama"

        if api_key or base_url:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=OPENAI_TIMEOUT_SECONDS,
                max_retries=OPENAI_MAX_RETRIES,
            )
        else:
            self.client = None
        self.model = model
        self.config: dict = config or {}

    def _get_prompt_addendum(self, key: str) -> str | None:
        try:
            ai_cfg = self.config.get("ai", {}) if isinstance(self.config, dict) else {}
            v = ai_cfg.get(key) if isinstance(ai_cfg, dict) else None
            if v is None:
                return None
            if not isinstance(v, str):
                return None
            vv = v.strip()
            return vv if vv else None
        except Exception:
            return None

    def _get_prompt_override(self, key: str) -> str | None:
        try:
            ai_cfg = self.config.get("ai", {}) if isinstance(self.config, dict) else {}
            v = ai_cfg.get(key) if isinstance(ai_cfg, dict) else None
            if v is None:
                return None
            if not isinstance(v, str):
                return None
            vv = v.strip()
            return vv if vv else None
        except Exception:
            return None

    def _safe_completion(self, messages: list, max_tokens: int = 500, temperature: float = 0) -> str:
        """
        Wrapper for OpenAI completions with timeout and error handling.
        Returns the response content or raises a descriptive exception.
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return (response.choices[0].message.content or "").strip()
        except APITimeoutError:
            raise TimeoutError(f"OpenAI API timed out after {OPENAI_TIMEOUT_SECONDS}s")
        except APIConnectionError as e:
            raise ConnectionError(f"Failed to connect to OpenAI API: {e}")
        except RateLimitError as e:
            raise RuntimeError(f"OpenAI rate limit exceeded: {e}")
        except Exception as e:
            raise RuntimeError(f"OpenAI API error: {type(e).__name__}: {e}")

    def analyse_news_sentiment(self, symbol, headlines):
        """
        Uses OpenAI to analyse the sentiment of recent real news headlines.
        Returns a score between -1 (very bearish) and 1 (very bullish).
        """
        if not headlines:
            raise ValueError("No headlines provided for AI sentiment analysis.")

        prompt = f"""
        Analyse the following news headlines for the stock symbol {symbol}.
        Provide a sentiment score between -1.0 (extremely negative) and 1.0 (extremely positive).
        Return ONLY the numerical score (no extra text).

        Headlines:
        {chr(10).join(headlines)}
        """

        raw = self._safe_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
        )

        try:
            score = float(raw)
        except ValueError as exc:
            raise ValueError(f"AI returned non-numeric sentiment output: {raw!r}") from exc

        logger.info(f"AI sentiment score for {symbol}: {score}")
        return score

    def analyse_reddit_sentiment(self, symbol_to_posts: dict[str, list[str]]) -> dict[str, dict]:
        """
        Uses OpenAI to estimate Reddit sentiment for provided symbols.
        Expects symbol_to_posts values to be short strings (already truncated).

        Returns mapping: symbol -> {sentiment, confidence, rationale}
        """
        if not symbol_to_posts:
            raise ValueError("No Reddit posts provided for AI analysis.")

        # Build prompt with strict JSON output requirement.
        lines = [
            "You are analysing Reddit posts to estimate crowd sentiment towards stock symbols.",
            "For each symbol, return a JSON object with:",
            "- symbol (string)",
            "- sentiment (number from -1.0 bearish to +1.0 bullish)",
            "- confidence (number 0.0 to 1.0)",
            "- rationale (short string, <= 140 chars)",
            "",
            "Return ONLY valid JSON (no markdown).",
            "",
            "Data:",
        ]
        for sym, posts in symbol_to_posts.items():
            lines.append(f"SYMBOL: {sym}")
            for i, p in enumerate(posts, start=1):
                lines.append(f"{i}. {p}")
            lines.append("")

        prompt = "\n".join(lines)

        raw = self._safe_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI returned non-JSON for Reddit sentiment: {raw!r}") from exc

        # Accept either list of objects or dict keyed by symbol.
        out: dict[str, dict] = {}
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol") or "").strip().upper()
                if not sym:
                    continue
                out[sym] = {
                    "sentiment": float(item.get("sentiment")),
                    "confidence": float(item.get("confidence")),
                    "rationale": str(item.get("rationale") or ""),
                }
        elif isinstance(parsed, dict):
            for sym, item in parsed.items():
                if not isinstance(item, dict):
                    continue
                sym_u = str(sym).strip().upper()
                out[sym_u] = {
                    "sentiment": float(item.get("sentiment")),
                    "confidence": float(item.get("confidence")),
                    "rationale": str(item.get("rationale") or ""),
                }
        else:
            raise ValueError(f"Unexpected JSON shape for Reddit sentiment: {type(parsed).__name__}")

        return out

    def decide_intraday_trade(
        self,
        *,
        symbol: str,
        exchange: str,
        currency: str,
        price: float | None,
        indicators: dict,
        headlines: list[str],
        reddit: dict | None,
        intraday: dict,
        fundamentals: dict | None = None,
        bar_momentum: dict | None = None,
        market_context: dict | None = None,
    ) -> dict:
        """
        Uses OpenAI to decide whether a symbol is a good intraday trade candidate.

        Returns a dict with:
        - decision: SHORTLIST | SKIP
        - confidence: 0.0..1.0
        - score: 0.0..1.0 (used for ranking vs other candidates)
        - sentiment: -1.0..1.0 (overall sentiment / catalyst bias)
        - rationale: short string
        - key_factors: list[str]
        - key_risks: list[str]
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set; cannot run AI trade decisions.")
        # AI can now make decisions purely on technical/fundamental data if no news is available

        payload = {
            "symbol": str(symbol),
            "exchange": str(exchange),
            "currency": str(currency),
            "price": price,
            "indicators": indicators,
            "news_headlines": headlines,
            "reddit": reddit,
            "intraday": intraday,
            "fundamentals": fundamentals,
            "bar_momentum": bar_momentum,
            "market_context": market_context,
        }

        system = build_shortlist_system_prompt(self.config)

        user = json.dumps(payload, ensure_ascii=False)

        raw = self._safe_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI returned non-JSON for trade decision: {raw!r}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(f"AI returned unexpected JSON type for trade decision: {type(parsed).__name__}")

        decision = str(parsed.get("decision") or "").strip().upper()
        if decision not in {"SKIP", "SHORTLIST"}:
            raise ValueError(f"AI returned invalid decision {decision!r}")

        confidence = float(parsed.get("confidence"))
        score = float(parsed.get("score"))
        sentiment = float(parsed.get("sentiment"))
        rationale = str(parsed.get("rationale") or "").strip()
        key_factors = parsed.get("key_factors")
        key_risks = parsed.get("key_risks")

        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"AI confidence out of range: {confidence}")
        if not (0.0 <= score <= 1.0):
            raise ValueError(f"AI score out of range: {score}")
        if not (-1.0 <= sentiment <= 1.0):
            raise ValueError(f"AI sentiment out of range: {sentiment}")
        if not rationale:
            raise ValueError("AI rationale is empty")
        if not isinstance(key_factors, list) or not all(isinstance(x, str) for x in key_factors):
            raise ValueError("AI key_factors must be a list of strings")
        if not isinstance(key_risks, list) or not all(isinstance(x, str) for x in key_risks):
            raise ValueError("AI key_risks must be a list of strings")

        return {
            "decision": decision,
            "confidence": confidence,
            "score": score,
            "sentiment": sentiment,
            "rationale": rationale,
            "key_factors": key_factors[:6],
            "key_risks": key_risks[:6],
        }

    def select_buys_from_shortlist(
        self,
        *,
        candidates: list[dict],
        max_new: int,
        budget_remaining: dict | None = None,
        market_context: dict | None = None,
    ) -> dict:
        """
        Stage 2: select which symbols to BUY from a shortlist.

        Returns a dict with:
        - selected_symbols: list[str] (priority order, length <= max_new)
        - rationale: short string summary
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set; cannot run AI buy selection.")
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a list")
        if not isinstance(max_new, int) or max_new < 0:
            raise ValueError("max_new must be a non-negative integer")
        if max_new == 0 or not candidates:
            return {"selected_symbols": [], "rationale": "No capacity or no shortlisted candidates."}

        # Ensure candidates are JSON-safe and symbols are present.
        cand_symbols: list[str] = []
        cleaned: list[dict] = []
        for c in candidates:
            if not isinstance(c, dict):
                continue
            sym = str(c.get("symbol") or "").strip().upper()
            if not sym:
                continue
            cand_symbols.append(sym)
            cleaned.append({**c, "symbol": sym})
        if not cleaned:
            return {"selected_symbols": [], "rationale": "No valid shortlisted candidates."}

        payload = {
            "max_new": int(max_new),
            "budget_remaining": budget_remaining or {},
            "candidates": cleaned,
            "market_context": market_context,
        }

        system = build_buy_selection_system_prompt(self.config)
        user = json.dumps(payload, ensure_ascii=False)

        raw = self._safe_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=700,
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI returned non-JSON for buy selection: {raw!r}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(f"AI returned unexpected JSON type for buy selection: {type(parsed).__name__}")

        selected_symbols = parsed.get("selected_symbols")
        if not isinstance(selected_symbols, list) or not all(isinstance(x, str) for x in selected_symbols):
            raise ValueError("AI selected_symbols must be a list of strings")

        selected_norm: list[str] = []
        seen: set[str] = set()
        allowed = set(cand_symbols)
        for s in selected_symbols:
            ss = str(s).strip().upper()
            if not ss or ss in seen:
                continue
            if ss not in allowed:
                raise ValueError(f"AI selected unknown symbol: {ss}")
            selected_norm.append(ss)
            seen.add(ss)
            if len(selected_norm) >= int(max_new):
                break

        rationale = str(parsed.get("rationale") or "").strip()
        if not rationale:
            rationale = "Selected from shortlist."

        return {"selected_symbols": selected_norm, "rationale": rationale[:250]}

    def review_position(
        self,
        *,
        symbol: str,
        exchange: str,
        currency: str,
        # Position details
        entry_price: float,
        current_price: float,
        quantity: int,
        unrealised_pnl: float,
        pnl_pct: float,
        peak_pnl_pct: float | None = None,
        drawdown_from_peak_pct: float | None = None,
        minutes_held: int,
        # Current order levels
        current_stop_loss: float | None,
        current_take_profit: float | None,
        distance_to_stop_pct: float | None,
        distance_to_tp_pct: float | None,
        # Current market data
        indicators: dict,
        bar_momentum: dict | None,
        fundamentals: dict | None = None,
        market_context: dict | None,
        # News/sentiment
        headlines: list[str],
        reddit: dict | None,
        # Opportunity cost
        top_candidates: list[dict] | None = None,
        # Config
        intraday: dict | None = None,
    ) -> dict:
        """
        Uses OpenAI to review an open position and decide on next action.

        Returns a dict with:
        - action: HOLD | SELL | ADJUST_STOP | ADJUST_TP
        - new_stop_loss: float | None (if ADJUST_STOP)
        - new_take_profit: float | None (if ADJUST_TP)
        - confidence: 0.0..1.0
        - urgency: 0.0..1.0 (how urgently this should be acted on)
        - rationale: short string
        - key_factors: list[str]
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set; cannot run AI position review.")

        payload = {
            "symbol": str(symbol),
            "exchange": str(exchange),
            "currency": str(currency),
            "position": {
                "entry_price": entry_price,
                "current_price": current_price,
                "quantity": quantity,
                "unrealised_pnl": unrealised_pnl,
                "pnl_pct": pnl_pct,
                "peak_pnl_pct": peak_pnl_pct,
                "drawdown_from_peak_pct": drawdown_from_peak_pct,
                "minutes_held": minutes_held,
            },
            "orders": {
                "current_stop_loss": current_stop_loss,
                "current_take_profit": current_take_profit,
                "distance_to_stop_pct": distance_to_stop_pct,
                "distance_to_tp_pct": distance_to_tp_pct,
            },
            "indicators": indicators,
            "bar_momentum": bar_momentum,
            "fundamentals": fundamentals,
            "market_context": market_context,
            "news_headlines": headlines,
            "reddit": reddit,
            "top_candidates": top_candidates,
            "intraday": intraday,
        }

        system = build_position_review_system_prompt(self.config)

        user = json.dumps(payload, ensure_ascii=False)

        raw = self._safe_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=600,
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI returned non-JSON for position review: {raw!r}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(f"AI returned unexpected JSON type: {type(parsed).__name__}")

        action = str(parsed.get("action") or "").strip().upper()
        if action not in {"HOLD", "SELL", "ADJUST_STOP", "ADJUST_TP"}:
            raise ValueError(f"AI returned invalid action {action!r}")

        new_stop_loss = parsed.get("new_stop_loss")
        new_take_profit = parsed.get("new_take_profit")
        confidence = float(parsed.get("confidence"))
        urgency = float(parsed.get("urgency", 0.5))
        rationale = str(parsed.get("rationale") or "").strip()
        key_factors = parsed.get("key_factors", [])

        # Validate
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"AI confidence out of range: {confidence}")
        if not (0.0 <= urgency <= 1.0):
            raise ValueError(f"AI urgency out of range: {urgency}")
        if action == "ADJUST_STOP" and new_stop_loss is None:
            raise ValueError("AI returned ADJUST_STOP without new_stop_loss")
        if action == "ADJUST_TP" and new_take_profit is None:
            raise ValueError("AI returned ADJUST_TP without new_take_profit")
        if new_stop_loss is not None:
            new_stop_loss = float(new_stop_loss)
        if new_take_profit is not None:
            new_take_profit = float(new_take_profit)

        return {
            "action": action,
            "new_stop_loss": new_stop_loss,
            "new_take_profit": new_take_profit,
            "confidence": confidence,
            "urgency": urgency,
            "rationale": rationale,
            "key_factors": key_factors[:5] if isinstance(key_factors, list) else [],
        }

    def review_order(
        self,
        *,
        symbol: str,
        order_id: int,
        order_type: str,  # BUY, SELL (action)
        order_side: str,  # STP, LMT, MKT
        order_quantity: int,
        order_price: float | None,  # The price on the order
        current_price: float | None,
        bid_price: float | None,
        ask_price: float | None,
        order_age_minutes: int,
        # Market context
        indicators: dict | None = None,
        volume_profile: dict | None = None,
        market_context: dict | None = None,
    ) -> dict:
        """
        Uses OpenAI to review a pending/unfilled order and decide on action.

        Returns a dict with:
        - action: KEEP | CANCEL | ADJUST_PRICE
        - new_price: float | None (required if ADJUST_PRICE)
        - confidence: 0.0..1.0
        - rationale: short string
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set; cannot run AI order review.")

        # Calculate price distance
        price_distance_pct = None
        if order_price is not None and current_price is not None and current_price > 0:
            price_distance_pct = ((order_price - current_price) / current_price) * 100

        # Calculate spread
        spread_pct = None
        if bid_price is not None and ask_price is not None and ask_price > 0:
            spread_pct = ((ask_price - bid_price) / ask_price) * 100

        payload = {
            "symbol": str(symbol),
            "order": {
                "order_id": order_id,
                "action": order_type,  # BUY or SELL
                "type": order_side,  # STP, LMT, MKT
                "quantity": order_quantity,
                "order_price": order_price,
                "age_minutes": order_age_minutes,
            },
            "market": {
                "current_price": current_price,
                "bid": bid_price,
                "ask": ask_price,
                "spread_pct": spread_pct,
                "price_distance_pct": price_distance_pct,
            },
            "indicators": indicators,
            "volume_profile": volume_profile,
            "market_context": market_context,
        }

        system = build_order_review_system_prompt(self.config)

        user = json.dumps(payload, ensure_ascii=False)

        raw = self._safe_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=400,
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI returned non-JSON for order review: {raw!r}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(f"AI returned unexpected JSON type: {type(parsed).__name__}")

        action = str(parsed.get("action") or "").strip().upper()
        if action not in {"KEEP", "CANCEL", "ADJUST_PRICE"}:
            raise ValueError(f"AI returned invalid order action {action!r}")

        new_price = parsed.get("new_price")
        confidence = float(parsed.get("confidence", 0.5))
        rationale = str(parsed.get("rationale") or "").strip()

        # Validate
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"AI confidence out of range: {confidence}")
        if action == "ADJUST_PRICE" and new_price is None:
            raise ValueError("AI returned ADJUST_PRICE without new_price")
        if new_price is not None:
            new_price = float(new_price)

        return {
            "action": action,
            "new_price": new_price,
            "confidence": confidence,
            "rationale": rationale,
        }

    def research_stock_vulnerability(self, symbol, description):
        """
        Uses AI to research potential risks or 'vulnerabilities' in a low-cost stock.
        """
        raise NotImplementedError("Stock vulnerability research is not implemented yet.")

