## Development guide (AI-first)

This document is a developer-focused explanation of how AutoTrader works, with an emphasis on the AI decision pipeline and the configuration surface. It is written as a long-form reference (you can reuse it as source material for an article).

### What AutoTrader is (in one paragraph)

AutoTrader is an AI-driven research and trading system built around Interactive Brokers (IBKR). Each cycle it builds a candidate universe (via IBKR scanners and optional manual/Reddit augmentation), enriches each symbol with market data and context, asks the AI to shortlist or skip candidates, then asks the AI to select buys from the shortlist, and finally places orders with protective exits and ongoing AI-driven position/order reviews.

---

## Architecture at a glance

AutoTrader runs as **two Python processes** plus a **React dashboard**:

- **Trader**: `main.py` (entrypoint) → `src/trader/runner.py` (trading loop)
  - Connects to IBKR (TWS/IB Gateway) via `ib_insync`
  - Builds a candidate universe and runs research
  - Calls the AI for decisions (shortlist, buy selection, reviews)
  - Places/cancels/modifies orders and writes audit/history to the database

- **API**: `api_server.py` → `src/api/app.py`
  - **History endpoints** are DB-backed (stable)
  - **Live endpoints** are IBKR-backed via a dedicated service thread (`src/api/ibkr_service.py`)
  - Streams events to the dashboard via SSE (`/api/events/stream`)

- **Dashboard**: `frontend/` (React + Vite)
  - Polls history endpoints and opens SSE for live events
  - Provides a Settings modal for runtime configuration
  - Shows status pills for API/IBKR/trader activity

### “Live vs history” is intentional

AutoTrader treats data sources explicitly:

- **Live (IBKR-owned)**: account values, positions, open orders  
  If IBKR is unavailable, live endpoints return **503**. There are no silent “fallbacks”.

- **History (DB-owned)**: research logs, trades, performance series, events, reviews  
  These remain available even if IBKR disconnects temporarily.

---

## The AI pipeline (the heart of the system)

AutoTrader’s AI decisioning is intentionally split into stages:

### Stage 0: universe selection (what the AI analyses)

Primary source: **IBKR scanner results**, filtered by:

- `trading.markets` (US/UK)
- `trading.max_share_price`, `trading.min_share_price`
- `trading.min_avg_volume`
- `trading.exclude_microcap` (filters trading class `SCM`)

User configurable “universe controls”:

- `trading.screener.max_candidates`
- `trading.screener.scan_codes`
- `trading.screener.include_symbols` / `exclude_symbols`
- `trading.screener.include_reddit_symbols` (optional augmentation from cached Reddit posts, based on conservative `$TICKER` parsing + IBKR contract qualification)

Code:
- Universe selection: `src/research/screener.py`
- Optional Reddit augmentation: `src/trader/runner.py`

### Stage 1: shortlist or skip (per symbol)

For each candidate, the trader gathers:

- **Market data** (historical bars) and computes indicators (ATR/RSI/Bollinger)
- **Momentum features** from recent bars
- **News headlines** (IBKR news when available)
- **Reddit context** (cached sentiment/mentions if enabled)
- **Basic market context** (e.g. SPY/QQQ snapshot when available)

Then the AI returns:

- `SHORTLIST` or `SKIP`
- a score/confidence and a human-readable rationale

Code:
- AI calls: `src/research/ai_researcher.py`
- Prompt templates + enforcement: `src/research/prompts.py`

### Stage 2: buy selection (from shortlist)

After the scan, the AI sees the **shortlisted set** and decides which symbols to buy **this cycle**, respecting high-level limits such as:

- `trading.max_new_positions_per_cycle`
- cash budgets by currency (based on `trading.cash_budget_tag`, `trading.max_cash_utilisation`, and per-currency reserves)

The point of stage 2 is to let the AI make portfolio-aware choices rather than deciding “BUY now” in isolation for each symbol.

### AI reviews (ongoing supervision)

AutoTrader also uses AI to:

- review open positions (hold/sell/adjust stops/TP)
- review open orders (keep/cancel/adjust price)

This keeps behaviour consistent: the AI is responsible for decisions, and the bot focuses on safe execution and observability.

---

## Prompts, strategies, and “what users can change”

### Where prompts live

Built-in prompt templates are defined in code:

- `src/research/prompts.py`

This file centralises:

- the human instructions that define each AI task (shortlist, buy selection, position review, order review)
- output contract enforcement (the system validates and parses structured outputs)

### Runtime-configurable strategy prompts

Prompt overrides are configurable via runtime configuration (Settings modal). Conceptually:

- the built-in prompt is the “Default strategy”
- custom strategies can override one or more prompt blocks

Users can change:

- `ai.shortlist_system_prompt`
- `ai.buy_selection_system_prompt`
- `ai.position_review_system_prompt`
- `ai.order_review_system_prompt`

The output schema is **not** exposed as a user-editable field; it is enforced by code so the bot remains predictable.

---

## AI provider configuration (OpenAI-compatible)

AutoTrader uses the OpenAI SDK but supports **OpenAI-compatible providers**:

- **OpenAI**:
  - set `OPENAI_API_KEY`
- **Compatible providers (e.g. Ollama)**:
  - set `OPENAI_BASE_URL` (e.g. `http://localhost:11434/v1`)
  - set `OPENAI_API_KEY` (often any value is accepted by local providers)

Model selection is configured in one place:

- `ai.model` in `config/config.yaml` (and can be overridden via runtime config)

Code:
- `src/research/ai_researcher.py` initialises the OpenAI client with `OPENAI_BASE_URL` support.

---

## Risk, execution, and safety constraints

AutoTrader is designed as a **long-only** system.

Key safety mechanisms:

- **No accidental shorts**:
  - caps sells to actual held quantity
  - cancels orphaned SELL orders
  - safety net to close any detected short positions

- **Microcap avoidance**:
  - filters low-price/low-volume candidates
  - optional trading-class exclusions (`SCM`) to reduce microcap compliance issues

- **Protective exits**:
  - stop-loss and take-profit set using ATR and configured risk parameters
  - OCA linking where appropriate to prevent conflicting orders

Code:
- Execution + order management: `src/trading/executor.py`
- Position/order review orchestration: `src/trading/position_manager.py`

---

## Configuration: how to run and customise

AutoTrader configuration is layered (in order):

1. `config/config.yaml` (base)
2. environment variables (operational overrides)
3. runtime config (DB-backed, editable via the dashboard)

### Local setup (recommended)

1) Copy secrets template:

```bash
cp config/secrets.env.example config/secrets.env
```

2) Start PostgreSQL:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

3) Run components (typical dev flow):

```bash
export AUTOTRADER_DATABASE_URL="postgresql://autotrader:autotrader@127.0.0.1:5432/autotrader"
export PYTHONPATH="$PYTHONPATH:."

conda run -n autotrader python api_server.py
conda run -n autotrader python main.py
cd frontend && npm install && npm run dev
```

### Common “knobs” to adjust

- **Universe**: `trading.screener.*`, `trading.markets`, price/volume filters
- **Spend limits**: `trading.max_cash_utilisation`, `trading.min_cash_reserve_by_currency`, `trading.cash_budget_tag`
- **Position sizing**: `trading.risk_per_trade`
- **Cycle timing**: `intraday.cycle_interval_seconds`, `intraday.cycle_interval_seconds_closed`
- **Strategy prompts**: runtime config strategies (Settings modal)

---

## Development workflow

### Testing

- Unit tests:

```bash
pytest
```

- Integration tests (real services: PostgreSQL + IBKR paper + OpenAI-compatible API):

```bash
pytest -m integration
```

### CI

GitHub Actions runs:

- Python unit tests (Linux)
- Frontend lint + build (Linux)

Workflow: `.github/workflows/ci.yml`

---

## Extending the system (common changes)

### Add a new runtime setting

1. Add a default in `config/config.yaml`
2. Add runtime validator in `src/utils/runtime_config.py`
3. Add UI control in `frontend/src/App.tsx` (Settings modal)
4. Apply the new setting in the relevant subsystem (screener, trader loop, executor, etc.)
5. Add/adjust tests in `tests/`

### Add or change an AI prompt

1. Update prompt templates in `src/research/prompts.py`
2. Update AI client usage in `src/research/ai_researcher.py` if needed
3. Ensure any structured output parsing remains strict
4. Expose a strategy override (if it should be user-configurable)


