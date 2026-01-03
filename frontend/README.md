# AutoTrader Dashboard (React + Vite)

This is the React dashboard for AutoTrader. It provides:

- Live monitoring (via SSE)
- History panels (DB‑backed)
- A Settings modal for runtime configuration (risk limits, markets, AI strategy prompts)

## Prerequisites

- Node.js 18+
- The AutoTrader API running locally on `127.0.0.1:8000`

The dev server proxies `/api/*` to the backend (see `vite.config.ts`).

## Development

```bash
npm install
npm run dev
```

Open the URL printed by Vite (usually `http://localhost:5173`).

## Build

```bash
npm run build
```

## Lint

```bash
npm run lint
```
