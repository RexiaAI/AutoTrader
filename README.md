# AutoTrader (AI‑driven research + trading)

AutoTrader is an AI‑driven stock research and trading system for **IBKR**. It focuses on scanning liquid, lower‑priced stocks, shortlisting candidates, and then selecting buys from that shortlist.

## Safety + disclaimer

- **This project can place real orders** if pointed at a live IBKR account. Use **Paper Trading** until you are fully confident.
- This repository is **not financial advice**. You are responsible for all trades and risk.

## What’s included

- **Trader engine** (`main.py`): scanning, research, AI decisions, order placement, and audit logging.
- **FastAPI server** (`api_server.py` → `src/api/app.py`): history endpoints backed by the database, plus live endpoints via a dedicated IBKR service thread.
- **React dashboard** (`frontend/`): monitoring, settings modal (runtime config), SSE live stream.
- **Runtime configuration (DB‑backed)**: update risk limits, markets, and AI strategy prompts without restarting.

## Prerequisites

- **IBKR TWS or IB Gateway** running with API access enabled (Paper Trading recommended).
- **Python 3.10+**.
- **Node.js 18+** (for the dashboard).
- **An OpenAI‑compatible API** (OpenAI, or a compatible provider such as Ollama).
- **PostgreSQL (recommended)**: local install or Docker.

## Quick start (local, recommended)

### 1) Set up environment variables

Copy the example file and set your AI provider details:

```bash
cp config/secrets.env.example config/secrets.env
```

### 2) Start PostgreSQL (Docker)

```bash
docker compose -f docker-compose.postgres.yml up -d
```

### 3) Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4) Run the trader

```bash
export PYTHONPATH="$PYTHONPATH:."
python main.py
```

### 5) Run the API

```bash
export PYTHONPATH="$PYTHONPATH:."
python api_server.py
```

### 6) Run the dashboard

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL printed in the terminal (usually `http://localhost:5173`).

## Configuration

AutoTrader is configured from three places (in order):

1. **Base YAML**: `config/config.yaml`
2. **Environment variables** (for a small set of operational overrides)
3. **Runtime config (DB‑backed)**: editable from the dashboard Settings modal, applied at the start of each trader cycle

### Secrets file (local)

- **Local secrets**: `config/secrets.env`
  - This file is **for local use** and is ignored by `.gitignore`.
  - Create it by copying `config/secrets.env.example`.

Required (pick one approach):
- **OpenAI**: set **`OPENAI_API_KEY`**.
- **OpenAI‑compatible (e.g. Ollama)**: set **`OPENAI_BASE_URL`** (and optionally **`OPENAI_API_KEY`**; a dummy value is fine for many local providers).

#### Using Ollama (OpenAI‑compatible)

If you want to run locally with Ollama:

- Set in `config/secrets.env`:
  - `OPENAI_BASE_URL=http://localhost:11434/v1`
  - `OPENAI_API_KEY=ollama`
- Set the model in **one place**:
  - `ai.model` in `config/config.yaml` (or via the dashboard Settings modal runtime config)

Any other OpenAI‑compatible proxy/provider works the same way: point `OPENAI_BASE_URL` at it, then change `ai.model`.

### Database

- **Recommended**: PostgreSQL
  - Set `AUTOTRADER_DATABASE_URL` (or `DATABASE_URL`) to a `postgresql://...` DSN.
  - The trader writes history; the API reads history and serves it to the dashboard.
- **Fallback**: SQLite (`autotrader.db`)
  - Used when no PostgreSQL DSN is configured.
  - Not recommended when running trader + API together (SQLite locking under concurrent writer/reader load).

### Environment variables (operational overrides)

These override a small set of values from `config/config.yaml`:

- **Broker**
  - `AUTOTRADER_BROKER_HOST`
  - `AUTOTRADER_BROKER_PORT`
  - `AUTOTRADER_BROKER_CLIENT_ID`
- **AI**
  - `AUTOTRADER_AI_MODEL` (default: `gpt-4.1-mini`)
- **Cycle timing**
  - `AUTOTRADER_CYCLE_INTERVAL_SECONDS`
- **PostgreSQL tuning (optional)**
  - `AUTOTRADER_PG_CONNECT_TIMEOUT_SECONDS`
  - `AUTOTRADER_PG_POOL_MIN`
  - `AUTOTRADER_PG_POOL_MAX`

### Runtime configuration (Settings modal)

Runtime config is stored in the database and applied dynamically. It is designed for:

- **Risk/spend limits** (e.g. max cash utilisation, per‑currency cash reserve)
- **Markets to trade** (e.g. US/UK)
- **Strategies** (select an active strategy; each strategy can override parameters)
- **AI strategy prompts**:
  - shortlist prompt (stage 1)
  - buy selection prompt (stage 2)
  - position review prompt
  - order review prompt

The **output schema is enforced in code**. The dashboard shows *instruction prompts* only; you do not manage JSON output formats.

### IBKR setup checklist

AutoTrader requires **TWS** or **IB Gateway** with API access enabled.

- **Paper trading ports**
  - TWS Paper: `7497` (default in `config/config.yaml`)
- **API settings**
  - Enable API / socket clients
  - Allow connections from localhost
- **Client IDs**
  - Trader uses `broker.client_id` (default `10`)
  - API uses `broker.client_id + 1000` (to avoid collisions)
  - API is single‑instance (lockfile in `api_server.py`)

### Base YAML quick reference (`config/config.yaml`)

This table covers the most commonly adjusted knobs. Anything marked **runtime** can be changed from the dashboard Settings modal (stored in the DB and applied at the start of each trader cycle).

| Path | Default | Purpose | Runtime? |
|---|---:|---|:---:|
| `broker.host` | `127.0.0.1` | Where TWS/IB Gateway is running. | No |
| `broker.port` | `7497` | IBKR API port (paper trading default for TWS). | No |
| `broker.client_id` | `10` | Trader’s IBKR clientId (API uses `+1000`). | No |
| `ai.model` | `gpt-4.1-mini` | Model name used by the trader (OpenAI‑compatible). | Yes |
| `trading.markets` | `["US","UK"]` | Which markets to scan/trade. | Yes |
| `trading.max_positions` | `10` | Maximum concurrent open positions. | Yes |
| `trading.max_new_positions_per_cycle` | `2` | Max new entries per scan cycle. | Yes |
| `trading.risk_per_trade` | `0.05` | Position sizing risk budget as a fraction of equity. | Yes |
| `trading.max_cash_utilisation` | `0.3` | Upper bound on cash allocation for new trades. | Yes |
| `trading.cash_budget_tag` | `TotalCashValue` | Which IBKR account value to treat as “cash budget”. | Yes |
| `trading.max_share_price` | `20.0` | Upper price bound for candidates (low‑cost focus). | Yes |
| `trading.min_share_price` | `2.0` | Lower price bound (avoid very cheap/illiquid names). | Yes |
| `trading.min_avg_volume` | `500000` | Liquidity filter (avoid thin volume / microcaps). | Yes |
| `trading.exclude_microcap` | `true` | Exclude IBKR trading class `SCM` to reduce Rule 144 headaches. | Yes |
| `trading.volatility_threshold` | `0.05` | Minimum ATR/price ratio for candidates (volatility filter). | Yes |
| `intraday.enabled` | `true` | Enables the intraday scan/trade cycle. | Yes |
| `intraday.cycle_interval_seconds` | `300` | Time between scan cycles when markets are open. | Yes |
| `intraday.cycle_interval_seconds_closed` | `1800` | Time between scan cycles when all configured markets are closed. | Yes |
| `intraday.bar_size` | `5 mins` | Bar size for historical data pulls. | No |
| `intraday.duration` | `2 D` | Historical window for indicator calculation. | No |
| `intraday.use_rth` | `true` | Use regular trading hours data. | No |
| `intraday.stop_atr_multiplier` | `2.0` | Stop distance in ATR multiples. | No |
| `intraday.take_profit_r` | `1.0` | Take‑profit distance in R multiples of risk. | No |
| `intraday.flatten_minutes_before_close` | `10` | Avoid new entries close to market close. | Yes |

## Testing

Run unit tests (no external services required):

```bash
pytest
```

Run unit tests with coverage:

```bash
pytest --cov=src --cov-report=term-missing
```

Run integration tests (requires real services: PostgreSQL + IBKR Paper + OpenAI key):

```bash
pytest -m integration
```

There is also a timeout wrapper:

```bash
python scripts/run_pytest_with_timeout.py --timeout-seconds 180 -- -q
```

## How it works

### Components

- **Trader** (`main.py`)
  - Scans candidates
  - Runs research + AI decisions
  - Places and manages orders
  - Writes history/audit records to the database
- **API** (`api_server.py` → `src/api/app.py`)
  - History endpoints: database‑backed (stable)
  - Live endpoints: IBKR‑backed (no fallbacks; returns **503** if IBKR is unavailable)
  - SSE stream: `/api/events/stream`
- **Dashboard** (`frontend/`)
  - Polls history + opens SSE for live event streaming
  - Provides the Settings modal to edit runtime config

### Trading flow (high level)

1. **Load configuration**
   - Trader loads `config/config.yaml`, applies env overrides, then applies runtime config from the DB.
2. **Scan**
   - The screener queries IBKR scanners and applies basic liquidity filters (min price/volume, optional microcap exclusion).
3. **Analyse each symbol**
   - Fetch historical bars from IBKR
   - Compute indicators (ATR/RSI/Bollinger) and momentum
   - Gather context (news/Reddit/fundamentals when available)
4. **AI stage 1: shortlist**
   - AI returns `SHORTLIST` or `SKIP` with scores and rationale.
5. **AI stage 2: buy selection**
   - At the end of the scan, AI selects which symbols to buy from the shortlist (up to the configured max new positions).
6. **Execution**
   - Places BUY orders and protective exits (stop‑loss / take‑profit) based on ATR and configured risk knobs.
7. **Position + order management**
   - Periodically reviews open positions and may hold/sell/adjust stops/TP.
   - Reviews unfilled orders and may keep/cancel/adjust.

## Troubleshooting (common)

- **Dashboard shows “IBKR not connected”**
  - The API and trader are running, but TWS/Gateway is not accepting connections on the configured host/port.
  - Live endpoints intentionally return **503** (no silent fallbacks).
- **Frontend dev server not reachable on `127.0.0.1`**
  - Vite binds to `localhost` by default. Use `http://localhost:5173/`.

## Docs

- `docs/ARCHITECTURE.md`: system design and failure modes
- `docs/RUNBOOK.md`: operational runbook (start/stop + debugging)


