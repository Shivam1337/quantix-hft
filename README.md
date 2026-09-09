# Cross-Exchange Funding Arbitrage Platform

This repository implements the system described in `plan.md`: a FastAPI market-data and risk backend, a React/Vite dashboard, PostgreSQL persistence, Redis caching, and a Docker Compose deployment.

## Quick start

```powershell
docker compose up --build
```

Open the dashboard at <http://localhost:8080>. The API is available at <http://localhost:8000>, with interactive docs at <http://localhost:8000/docs>.

The default deployment reads live mark, book, and open-interest values from Hyperliquid, Aevo, and Lighter for execution and liquidity checks only. Funding rates are never taken from live market snapshots. Each adapter separately imports exchange-confirmed funding history into the immutable `funding_settlements` table; only aligned, contiguous cycles with the configured minimum history are eligible for opportunity ranking, simulation, and accounting. Live WebSockets are still used for prices and liquidity, batched for 500 ms, and published through Redis; the API relays them to clients over `/ws/market`.

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
- `GET /api/v1/funding-history` for exchange-confirmed settlement cycles persisted in PostgreSQL.
- `GET /api/v1/funding-pending` for cycles awaiting official confirmation; pending cycles contribute no P&L.
- `POST /api/v1/positions/open` and `POST /api/v1/positions/{id}/close` for paper paired execution.
- `POST /api/v1/simulator` for projected hourly cashflow, fees, and return. Simulator capital is total margin across both legs; the default is $50 ($25 per leg) at 3x leverage, producing $75 notional per leg.
- `GET/PATCH /api/v1/settings` for APR, open-interest, basis, webhook, and auto-unwind controls.
- `GET/POST/DELETE /api/v1/wallet` for a process-memory-only wallet import, read-only balance refresh, and removal.
- `GET /api/v1/positions`, `GET /api/v1/logs`, and `GET /api/v1/health`.

The Settings page can import an Ethereum private key and derive its public address. Hyperliquid and Lighter balances are queried without sending the private key to either exchange; Aevo is shown as requiring its own API credentials. The key is never stored in the database or returned by the API, and the wallet panel does not sign or submit orders. Use this only on a trusted local deployment. `LIVE_TRADING_ENABLED=false` remains the default.

Risk checks count negative funding by UTC funding hour. A basis breach closes immediately when auto-unwind is enabled; a negative net APR for two consecutive funding hours also closes and records an alert.

## Fee and funding-rate sources

All fee estimates are applied to both legs at open and close: Lighter maker/taker are 0 bps, Hyperliquid execution is restricted to post-only limits at 1.5 bps maker, and Aevo is modeled as market/taker at 8 bps using its standard perpetual fee schedule. Aevo’s standard maker fee is 5 bps; the current paper execution policy uses taker orders. See [Aevo perpetual fees](https://www.aevo.xyz/docs/aevo-products/aevo-exchange/fees/perpetuals-fees.md).

Funding values are sourced from Hyperliquid `fundingHistory`, Aevo `funding-history`, and Lighter `fundings` history endpoints. The opportunity calculator uses the median spread of aligned confirmed cycles, subtracts pair fees, and refuses to rank a pair when history is missing or has a cycle gap. The ledger records a payment only after both legs are present in `funding_settlements`; otherwise it records a pending cycle and never falls back to a live estimate.
