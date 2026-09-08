# Cross-Exchange Funding Arbitrage Platform

This repository implements the system described in `plan.md`: a FastAPI market-data and risk backend, a React/Vite dashboard, PostgreSQL persistence, Redis caching, and a Docker Compose deployment.

## Quick start

```powershell
docker compose up --build
```

Open the dashboard at <http://localhost:8080>. The API is available at <http://localhost:8000>, with interactive docs at <http://localhost:8000/docs>.

The default deployment reads public live funding, mark, book, and open-interest values from Hyperliquid, Aevo, and Lighter. It records one PostgreSQL funding snapshot per venue/symbol/funding cycle, seeds through REST, then consumes Hyperliquid, Aevo book-ticker, and Lighter market-stats WebSockets, batches updates for 500 ms, and publishes them through Redis; the API relays them to clients over `/ws/market`. Each adapter retains its configured REST fallback, and the 30-second polling loop is available as an opt-out via `ENABLE_EXCHANGE_WEBSOCKETS=false`.

Compose uses a dedicated `funding_arbitrage_postgres_data` volume so it does not collide with an unrelated Postgres volume in the same Docker project directory. Keep the volume when upgrading; removing it deletes persisted trade and funding history.

## Local development

Backend tests and static checks run without Docker:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
pytest -q
ruff check backend
```

For the dashboard:

```powershell
cd frontend
npm install
npm run build
```

## Configuration

Copy `.env.example` to `.env` when running the backend directly. The exchange adapters are read-only and use the configured real symbols; `LIVE_TRADING_ENABLED=false` keeps wallet signing and order submission disabled. Live wallet signing and order submission remain behind the explicit `LIVE_TRADING_ENABLED` boundary and are not enabled by Compose.

The core API includes:

- `GET /api/v1/opportunities` for filtered, fee-adjusted opportunities.
- `GET /api/v1/funding-history` for cycle-level funding snapshots persisted in PostgreSQL.
- `POST /api/v1/positions/open` and `POST /api/v1/positions/{id}/close` for paper paired execution.
- `POST /api/v1/simulator` for projected hourly cashflow, fees, and return.
- `GET/PATCH /api/v1/settings` for APR, open-interest, basis, webhook, and auto-unwind controls.
- `GET /api/v1/positions`, `GET /api/v1/logs`, and `GET /api/v1/health`.

Risk checks count negative funding by UTC funding hour. A basis breach closes immediately when auto-unwind is enabled; a negative net APR for two consecutive funding hours also closes and records an alert.

## Fee and funding-rate sources

All fee estimates are applied to both legs at open and close: Lighter maker/taker are 0 bps, Hyperliquid execution is restricted to post-only limits at 1.5 bps maker, and Aevo is modeled as market/taker at 8 bps using its standard perpetual fee schedule. Aevo’s standard maker fee is 5 bps; the current paper execution policy uses taker orders. See [Aevo perpetual fees](https://www.aevo.xyz/docs/aevo-products/aevo-exchange/fees/perpetuals-fees.md).

Funding values are stored with the exchange-native rate, the hourly interval used by the calculator, the cycle marker, and the observation timestamp. Aevo and Lighter publish hourly funding; Hyperliquid’s current perpetual funding feed is also treated as hourly.
