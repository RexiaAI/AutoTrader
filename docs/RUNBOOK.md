# AutoTrader Runbook

This is the operational runbook for running AutoTrader (trader + API + dashboard) and diagnosing issues.

## Prerequisites

- **Conda environment**: `autotrader`
- **PostgreSQL** running locally (recommended)
- **IBKR TWS / IB Gateway** running in **Paper Trading** mode with API enabled

## Configuration

### Config file
- Main config: `config/config.yaml`

### Environment variables
- Secrets file (recommended for local runs):
  - Copy `config/secrets.env.example` → `config/secrets.env`
  - **Never commit** `config/secrets.env` (it is ignored via `.gitignore`)

- **Database** (required for PostgreSQL mode):
  - `AUTOTRADER_DATABASE_URL="postgresql://autotrader:autotrader@127.0.0.1:5432/autotrader"`

- **Optional overrides** (handy for local runs):
  - `AUTOTRADER_BROKER_HOST` (default from YAML)
  - `AUTOTRADER_BROKER_PORT` (default from YAML)
  - `AUTOTRADER_BROKER_CLIENT_ID` (default from YAML)
  - `AUTOTRADER_AI_MODEL` (default from YAML)
  - `AUTOTRADER_CYCLE_INTERVAL_SECONDS` (default from YAML intraday section)

## Start/stop procedures

### Start PostgreSQL (Docker)

From the project root:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

### Start the API

```bash
export AUTOTRADER_DATABASE_URL="postgresql://autotrader:autotrader@127.0.0.1:5432/autotrader"
conda run -n autotrader python api_server.py
```

Notes:
- The API starts on `127.0.0.1:8000`.
- Live IBKR endpoints **do not fall back** to the database; they return **503** if IBKR is not connected.

### Start the trader

```bash
export AUTOTRADER_DATABASE_URL="postgresql://autotrader:autotrader@127.0.0.1:5432/autotrader"
export PYTHONPATH="$PYTHONPATH:."
conda run -n autotrader python main.py
```

### Start the dashboard (React dev)

```bash
cd frontend
npm install
npm run dev
```

## Health checks

### API health
- Endpoint: `GET /api/health`
- Expected:
  - `db_ok: true`
  - `status: ok`

If `db_ok: false`, the API is degraded and DB-backed endpoints may fail.

### IBKR live status
- Live endpoints:
  - `GET /api/live/account-summary`
  - `GET /api/live/positions`
  - `GET /api/live/open-orders`

Expected:
- If IBKR connected: **200** with JSON arrays
- If IBKR not connected/ready: **503** with a short error `detail`

### SSE stream
- Endpoint: `GET /api/events/stream?after_id=<n>`
- Expected:
  - Continuous SSE with `data: {...}` payloads
  - Periodic keep-alives (`: keep-alive`)

## Known failure modes + diagnostics

### 1) Live endpoints returning 503

Meaning: API cannot talk to IBKR. This is expected behaviour (no fallbacks).

Check:
- TWS/Gateway is running in Paper Trading
- API settings allow connections
- `config/config.yaml` broker host/port match TWS/Gateway
- No clientId collisions (trader uses `broker.client_id`, API uses `broker.client_id + 1000`)

### 2) “Stream error” in the dashboard

Meaning: SSE dropped. The dashboard should reconnect automatically.

Check:
- `GET /api/health` (DB ok?)
- `GET /api/history/events?limit=10` returns quickly

### 3) Postgres connection hangs / long timeouts

Mitigation included:
- Postgres connections have an enforced **connect timeout** (default 3s).
  - Override with `AUTOTRADER_PG_CONNECT_TIMEOUT_SECONDS`.

If Postgres is unreachable:
- `/api/health` will show `db_ok: false`
- Contract tests will fail fast.

## Integration tests (real services)

Run in the `autotrader` conda environment.

### Contract tests (DB + SSE + live behaviour)

```bash
export AUTOTRADER_DATABASE_URL="postgresql://autotrader:autotrader@127.0.0.1:5432/autotrader"
conda run -n autotrader python scripts/run_pytest_with_timeout.py --timeout-seconds 180 -- -q -m integration -vv
```

Notes:
- These tests require **PostgreSQL**.
- The live tests require **IBKR paper** to be running and reachable.
- Some tests require **OpenAI** access (set `OPENAI_API_KEY` via `config/secrets.env`).
- The runner enforces a wall-clock timeout to prevent hangs.


